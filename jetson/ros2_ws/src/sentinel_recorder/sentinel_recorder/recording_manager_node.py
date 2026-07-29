#!/usr/bin/env python3
"""이벤트 녹화 관리자 (S15P11A301-123, 명세 32-5).

`sentinel_streaming`의 링 writer가 만든 조각을 모아 이벤트 MP4를 만든다. 노드
이름은 32-5가 쓰는 `recording_manager`를 따른다.

## 별 프로세스인 이유

완료 조건이 "녹화를 인위적으로 실패시켜도 관제 스트리밍과 AI가 유지된다"이다.
같은 프로세스면 MP4 생성 실패나 디스크 오류가 GStreamer 파이프라인 재구성을
유발해 스트리밍까지 끊긴다. `index.json`을 경계로 두면 이 노드를 `kill -9`해도
스트리밍은 모른다.

## 트리거

`/perception/encounter`의 `std_msgs/String`에 담긴 JSON이며 계약은
`common/schemas/encounter.schema.json`이다. AI 탐지 노드(S15P11A301-43)가 아직
없으므로 개발 중에는 `tools/trigger_encounter.py`로 같은 형식을 발행해 검증한다.

`phase`만 보고 전이한다. 사람 수와 위치는 보고서용이다.

## 조각을 계속 모으는 이유

이벤트가 시작되면 확정 직전 3초를 가져오고, 그 뒤로 완료되는 조각을 주기적으로
가져온다. 링 버퍼가 8초만 보관하므로 이벤트가 8초를 넘으면 뒤늦게 모을 수 없다.
5분(MAX_DURATION)까지 가는 이벤트를 담으려면 진행 중에 계속 가져와야 한다.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .event_finalizer import (
    FINAL_NAME,
    PARTIAL_SUFFIX,
    REPORT_NAME,
    EventFinalizer,
    FinalizeError,
    read_report,
    write_report,
)
from .pending_store import PendingStore, UPLOAD_STATE_PENDING
from .segment_store import Segment, SegmentStore, format_utc, parse_utc
from .state_machine import Phase, RecordingStateMachine


class RecordingManagerNode(Node):
    def __init__(self) -> None:
        super().__init__('recording_manager')

        self.declare_parameter('encounter_topic', '/perception/encounter')
        self.declare_parameter(
            'buffer_directory', '/var/lib/sentinel/media/buffer'
        )
        self.declare_parameter(
            'pending_directory', '/var/lib/sentinel/media/pending'
        )
        self.declare_parameter('segment_seconds', 1)
        self.declare_parameter('pre_seconds', 3)
        self.declare_parameter('post_seconds', 3)
        self.declare_parameter('no_response_timeout_seconds', 30)
        self.declare_parameter('max_event_seconds', 300)
        self.declare_parameter('max_pending_seconds', 1800)
        self.declare_parameter('encoder_bitrate_kbps', 2500)
        self.declare_parameter('collect_period_seconds', 0.5)
        # 이벤트를 마감하면 mission_manager에 알린다(S15P11A301-139). 26.3의
        # REPORTING → EXPLORING 전이가 이 신호로 일어난다.
        self.declare_parameter('mission_signal_topic', '/mission/signal')

        self.segments = SegmentStore(self._param('buffer_directory'))
        self.pending = PendingStore(
            self._param('pending_directory'),
            int(self._param('max_pending_seconds')),
            int(self._param('encoder_bitrate_kbps')),
        )
        self.finalizer = EventFinalizer(
            segment_seconds=int(self._param('segment_seconds')),
            thumbnail_offset_seconds=float(self._param('pre_seconds')),
        )
        self.machine = RecordingStateMachine(
            post_recording_seconds=int(self._param('post_seconds')),
            no_response_timeout_seconds=int(
                self._param('no_response_timeout_seconds')
            ),
            max_event_seconds=int(self._param('max_event_seconds')),
        )

        try:
            self.pending.prepare()
        except OSError as error:
            self.get_logger().error(
                f'pending 디렉터리를 만들 수 없다({error}). 이벤트를 저장할 수 없다.'
            )

        # 진행 중 이벤트의 작업 디렉터리와 수집한 조각.
        self.work_directory: Path | None = None
        self.collected: dict[int, Segment] = {}
        self.media_id: str | None = None

        self.status_pub = self.create_publisher(String, '~/status', 10)
        # mission_manager가 RELIABLE로 구독한다. 잃으면 REPORTING에서 못 나오고
        # 다음 사람을 찾지 못한다(S15P11A301-139).
        self.signal_pub = self.create_publisher(
            String, self._param('mission_signal_topic'), 10
        )
        self.create_subscription(
            String, self._param('encounter_topic'), self._on_encounter, 10
        )
        self.create_timer(
            float(self._param('collect_period_seconds')), self._on_tick
        )

        self._recover_on_boot()

        self.get_logger().info(
            f'recording_manager 시작. buffer={self.segments.buffer_directory} '
            f'pending={self.pending.directory} '
            f'pre={self._param("pre_seconds")}s post={self._param("post_seconds")}s '
            f'상한={self.pending.cap_bytes / 1e6:.0f}MB'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 부팅 복구 (32-5)
    # ------------------------------------------------------------------

    def _recover_on_boot(self) -> None:
        """전원 차단 후 남은 `.partial`과 조각을 정리한다.

        `.partial`이 있다는 것은 재생 검사를 통과하지 못했다는 뜻이다. 검사를
        통과한 파일만 최종 이름을 갖기 때문이다. 그래서 복구를 시도하지 않고
        `CORRUPT`로 표시한다.

        재다중화를 다시 시도하지 않는 이유는 조각이 이미 사라졌을 수 있고, 남아
        있어도 왜 검사에 실패했는지 알 수 없기 때문이다. 깨진 영상을 업로드하는
        것보다 깨졌다고 기록하는 편이 낫다.
        """
        if not self.pending.directory.is_dir():
            return

        recovered = 0
        corrupted = 0
        for child in sorted(self.pending.directory.iterdir()):
            if not child.is_dir():
                continue
            partials = list(child.glob(f'*{PARTIAL_SUFFIX}'))
            final = child / FINAL_NAME

            if final.exists():
                # 정상 완료. 남은 조각만 정리한다.
                if self.pending.cleanup_segments(child):
                    recovered += 1
                continue

            # mtime으로 정렬해야 한다. 파일명으로 정렬하면 순서가 뒤섞인다.
            #
            # 링 버퍼가 max-files로 파일명을 순환시키므로 seg_000000이 가장 오래된
            # 조각이라는 보장이 없다. 이름 순으로 이어붙이면 시간이 뒤섞인 MP4가
            # 만들어지고, 재생 검사는 H.264 패킷이 읽히기만 하면 통과하므로 잡히지
            # 않는다. 복구 시험에서 사전 영상이 -25.6초로 나와서 발견했다.
            leftover_segments = sorted(
                child.glob('*.ts'), key=lambda path: path.stat().st_mtime
            )

            if partials:
                # `.partial`이 있다는 것은 재생 검사를 통과하지 못했다는 뜻이다.
                # 검사를 통과한 파일만 최종 이름을 갖기 때문이다. 왜 실패했는지
                # 알 수 없으므로 재시도하지 않고 CORRUPT로 표시한다. 깨진 영상을
                # 업로드하는 것보다 깨졌다고 기록하는 편이 낫다.
                for partial in partials:
                    partial.unlink(missing_ok=True)
                self.pending.cleanup_segments(child)
                self._write_corrupt_report(
                    child, '재생 검사를 통과하지 못한 partial 파일이 남아 있었다'
                )
                corrupted += 1
                continue

            if leftover_segments:
                # 조각은 있는데 MP4가 없다. MP4 생성 전에 프로세스가 죽었다.
                # 진행 중 보고서에 detectedAt이 있으면 재시도할 수 있다.
                if self._retry_finalize(child, leftover_segments):
                    recovered += 1
                else:
                    corrupted += 1
                continue

            # 영상도 partial도 조각도 없다. 보고서만 있으면 DISK_FULL이나
            # 실패로 마감된 이벤트다. 아무것도 없으면 빈 디렉터리이므로 지운다.
            if not (child / REPORT_NAME).exists():
                try:
                    child.rmdir()
                except OSError:
                    pass

        if recovered or corrupted:
            self.get_logger().warn(
                f'부팅 복구: 조각 정리 {recovered}건, CORRUPT 표시 {corrupted}건'
            )

    def _retry_finalize(self, directory: Path, segment_files: list[Path]) -> bool:
        """남은 조각으로 MP4 생성을 다시 시도한다 (32-5 복구).

        진행 중 보고서의 `detectedAt`이 있어야 사전·사후 구간을 계산할 수 있다.
        없으면 어떤 이벤트였는지 알 수 없으므로 CORRUPT로 표시한다.

        조각 메타데이터(`sequence`, `firstPts`)는 링 버퍼의 현재 `index.json`에
        없을 수 있다. 링이 이미 지나갔기 때문이다. 그래서 파일명에서 id를 읽고
        파일 순서로 sequence를 대신한다. 연속성 검사가 약해지지만, 조각을 버리는
        것보다 낫다. 재생 검사가 최종 방어선이다.
        """
        report_path = directory / REPORT_NAME
        try:
            report = read_report(report_path)
            detected_at = parse_utc(str(report['detectedAt']))
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            self.pending.cleanup_segments(directory)
            self._write_corrupt_report(
                directory,
                '조각은 남았으나 detectedAt을 알 수 없어 복구할 수 없다',
            )
            return False

        segments: list[Segment] = []
        for path in segment_files:
            # 파일명이 seg_<sequence>.ts 이므로 거기서 sequence를 읽는다. 정상
            # 경로가 sequence로 이름을 만들기 때문이다. 옛 형식(링 파일명 그대로)
            # 이면 파일 순서를 sequence로 쓴다.
            try:
                order = int(path.stem.split('_')[-1])
            except ValueError:
                order = segment_files.index(path) + 1
            stat = path.stat()
            # 파일 mtime을 조각 종료 시각으로 쓴다. 정확하지 않지만 순서와
            # 대략의 길이를 잡는 데는 쓸 수 있다.
            ended = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            segment_seconds = int(self._param('segment_seconds'))
            segments.append(
                Segment(
                    segment_id=int(path.stem.split('_')[-1]),
                    sequence=order,
                    started_at=ended - timedelta(seconds=segment_seconds),
                    ended_at=ended,
                    duration_ms=segment_seconds * 1000,
                    first_pts=None,
                    first_frame_key=True,
                    # local_filename이 f'seg_{sequence:08d}.ts'를 만든다.
                    # 위에서 sequence를 파일명에서 읽었으므로 둘이 일치한다.
                    # 옛 형식 파일이면 일치하지 않으므로 아래에서 이름을 맞춘다.
                    path=f'buffer/{path.name}',
                )
            )

        # 파일명이 옛 형식(링 파일명 그대로)이면 sequence 기준 이름으로 바꾼다.
        # concat 목록이 local_filename을 쓰므로 이름이 맞아야 한다.
        for segment in segments:
            expected = directory / segment.local_filename
            actual = directory / Path(segment.path).name
            if actual != expected and actual.exists() and not expected.exists():
                actual.rename(expected)

        media_id = str(report.get('mediaId') or uuid.uuid4())
        try:
            result = self.finalizer.finalize(
                segments,
                directory,
                encounter_id=str(report.get('encounterId', directory.name)),
                media_id=media_id,
                detected_at=detected_at,
                end_reason='RECOVERED',
                person_count=int(report.get('personCount') or 0),
                mission_id=report.get('missionId'),
            )
        except (FinalizeError, OSError, subprocess.TimeoutExpired) as error:
            self.pending.cleanup_segments(directory)
            self._write_corrupt_report(
                directory, f'복구 중 MP4 생성 실패: {error}'
            )
            return False

        self.pending.cleanup_segments(directory)
        # 복구된 영상임을 명시한다. 사전·사후 구간이 조각 mtime 기반이라 정확도가
        # 정상 경로보다 낮다는 사실을 관제가 알아야 한다.
        try:
            recovered_report = read_report(directory / REPORT_NAME)
            recovered_report['mediaState'] = 'LOCAL'
            recovered_report['uploadState'] = UPLOAD_STATE_PENDING
            recovered_report['recovered'] = True
            recovered_report['recoveredAt'] = format_utc(self._now())
            recovered_report['recoveryNote'] = (
                '프로세스가 MP4 생성 전에 종료돼 남은 조각으로 복구했다. '
                '조각 시각을 파일 mtime으로 추정했으므로 coverage 값의 정확도가 '
                '정상 경로보다 낮다.'
            )
            write_report(directory / REPORT_NAME, recovered_report)
        except (OSError, json.JSONDecodeError):
            pass

        self.get_logger().warn(
            f'복구 성공: {directory.name[:8]} '
            f'{result.size_bytes / 1e6:.2f}MB {result.duration_seconds:.1f}초 '
            f'조각 {len(segments)}개'
        )
        return True

    def _write_corrupt_report(self, directory: Path, detail: str) -> None:
        report_path = directory / REPORT_NAME
        try:
            report = read_report(report_path)
        except (OSError, json.JSONDecodeError):
            report = {'schemaVersion': '1.0', 'encounterId': directory.name}
        report['mediaState'] = 'CORRUPT'
        report['mediaStateDetail'] = (
            f'{detail}. 전원 차단이나 프로세스 강제 종료로 보인다.'
        )
        report['recoveredAt'] = format_utc(self._now())
        write_report(report_path, report)

    # ------------------------------------------------------------------
    # 트리거
    # ------------------------------------------------------------------

    def _on_encounter(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'encounter JSON 해석 실패: {error}')
            return

        # 31-5 봉투로 감싸 왔으면 본문을 꺼낸다. 봉투 없이 본문만 보내는 것도
        # 받아준다. 젯슨 내부 토픽이라 두 형태가 다 올 수 있다.
        data = payload.get('data') if isinstance(payload.get('data'), dict) else payload

        try:
            encounter_id = str(data['encounterId'])
            phase = Phase(str(data['phase']))
            detected_at = parse_utc(str(data['detectedAt']))
        except (KeyError, ValueError) as error:
            self.get_logger().warn(
                f'encounter 필수 필드 오류({error}). '
                'common/schemas/encounter.schema.json 을 확인한다.'
            )
            return

        person_count = int(data.get('personCount') or 0)
        mission_id = data.get('missionId')
        now = self._now()

        was_idle = self.machine.event is None
        active = self.machine.event.encounter_id if self.machine.event else None
        transition = self.machine.on_encounter(
            encounter_id, phase, detected_at, now, person_count, mission_id
        )
        if transition is None:
            # 무시한 이유를 남긴다. 로그가 없으면 "신호를 못 받았다"와 "받았지만
            # 무시했다"를 구분할 수 없다. 실제로 이것 때문에 트리거 도구가
            # 고장난 줄 알고 한참 찾았다.
            if active is not None and active != encounter_id:
                self.get_logger().info(
                    f'{phase.value} 무시: 다른 이벤트가 진행 중이다 '
                    f'(진행={active[:8]}, 수신={encounter_id[:8]}). '
                    '32-6에 따라 동시에 하나만 녹화한다.'
                )
            elif active is None:
                self.get_logger().info(
                    f'{phase.value} 무시: 진행 중 이벤트가 없다 '
                    f'(수신={encounter_id[:8]}). CONFIRMED가 먼저 와야 한다.'
                )
            else:
                self.get_logger().debug(
                    f'{phase.value} 상태 변화 없음 (상태={self.machine.state.value})'
                )
            return

        self.get_logger().info(f'{transition} (encounter={encounter_id[:8]})')
        if was_idle and self.machine.event is not None:
            self._begin_event()
        self._publish_status(transition)

    def _begin_event(self) -> None:
        """작업 디렉터리를 만들고 확정 직전 3초를 가져온다 (32-5 이벤트 시작)."""
        event = self.machine.event
        assert event is not None

        # UUID여야 한다. 백엔드가 UploadUrlRequest에서 UUID로 받고
        # media_assets.id 가 UUID PRIMARY KEY 다(31-10, S15P11A301-126).
        #
        # 전에는 `m_{hex[:12]}` 형식이었다. media-upload-request.schema.json 의
        # mediaId에 pattern이 없어 계약이 양쪽을 묶지 못했고, 실물 업로드에서
        # 400 "잘못된 입력값입니다"로 드러났다(S15P11A301-124).
        self.media_id = str(uuid.uuid4())
        self.collected = {}

        # 완성된 이벤트 디렉터리를 재사용하면 안 된다.
        #
        # 이 노드가 재시작하면 상태 머신이 비어 있지만 AI는 그것을 모른다. 같은
        # encounterId로 CONFIRMED가 다시 오면 새 이벤트로 처리되고, 디렉터리를
        # 그대로 쓰면 이미 만들어 둔 event.mp4와 report.json을 덮어쓴다. 복구
        # 시험에서 실제로 복구된 영상이 진행 중 상태로 되돌아가는 것을 봤다.
        #
        # 접미사를 붙여 둘 다 남긴다. 관제가 같은 encounterId의 영상 두 개를 보는
        # 편이 하나를 잃는 것보다 낫다.
        base = self.pending.directory / event.encounter_id
        candidate = base
        suffix = 1
        while (candidate / FINAL_NAME).exists():
            suffix += 1
            candidate = self.pending.directory / f'{event.encounter_id}_{suffix}'
        if candidate != base:
            self.get_logger().warn(
                f'{base.name}에 이미 완성된 영상이 있다. '
                f'{candidate.name}에 새로 기록한다. '
                '노드 재시작 후 같은 encounterId가 다시 온 것으로 보인다.'
            )

        self.work_directory = candidate
        try:
            self.work_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.get_logger().error(f'이벤트 디렉터리 생성 실패: {error}')
            self.work_directory = None
            return

        # 진행 중 보고서를 먼저 쓴다.
        #
        # 이것이 없으면 프로세스가 강제 종료됐을 때 조각만 남고 그 조각이 어떤
        # 이벤트였는지 알 수 없다. detectedAt을 모르면 MP4를 만들어도 사전·사후
        # 구간을 판정할 수 없고, 보고서를 만들 수도 없어 관제에 아무것도 남지
        # 않는다. 실제로 VID-07 시험에서 조각 8개만 남고 방치되는 것을 봤다.
        #
        # 미리 써두면 부팅 복구가 이 값으로 MP4 생성을 재시도할 수 있다. 32-5가
        # "복구하거나 CORRUPT로 표시한다"고 한 것의 앞쪽이다.
        self._write_in_progress_report(event)

        pre_seconds = int(self._param('pre_seconds'))
        since = self.segments.pre_roll_start(event.detected_at, pre_seconds)
        pre = self.segments.segments_covering(since)
        gathered = self._collect(pre)

        if gathered:
            span = (pre[-1].ended_at - pre[0].started_at).total_seconds()
            self.get_logger().info(
                f'사전 영상 {gathered}조각 약 {span:.1f}초 확보 '
                f'(목표 {pre_seconds}초, 허용오차 -0에서 +1초)'
            )
        else:
            # 링이 이미 지웠거나 아직 조각이 없다. 이벤트를 포기하지 않고 계속
            # 모은다. 사후 영상만이라도 남기는 편이 낫다.
            self.get_logger().warn(
                '사전 영상 조각을 가져오지 못했다. 링 버퍼 길이와 '
                'detectedAt 시각을 확인한다.'
            )

    def _write_in_progress_report(self, event) -> None:
        """녹화가 시작됐다는 사실과 메타데이터를 즉시 남긴다."""
        if self.work_directory is None:
            return
        report = {
            'schemaVersion': '1.0',
            'encounterId': event.encounter_id,
            'mediaId': self.media_id,
            'missionId': event.mission_id,
            'detectedAt': format_utc(event.detected_at),
            'personCount': event.person_count,
            'mediaState': 'RECORDING',
            'startedAt': format_utc(event.started_at),
        }
        try:
            write_report(self.work_directory / REPORT_NAME, report)
        except OSError as error:
            self.get_logger().warn(f'진행 중 보고서 기록 실패: {error}')

    def _collect(self, segments: list[Segment]) -> int:
        if self.work_directory is None:
            return 0
        gathered = 0
        for segment in segments:
            if segment.sequence in self.collected:
                continue
            if self.segments.collect(segment, self.work_directory) is None:
                continue
            self.collected[segment.sequence] = segment
            gathered += 1
        return gathered

    # ------------------------------------------------------------------
    # 주기 처리
    # ------------------------------------------------------------------

    def _on_tick(self) -> None:
        now = self._now()

        # 진행 중이면 새로 완료된 조각을 계속 가져온다. 링 버퍼가 8초만 보관하므로
        # 나중에 한 번에 모을 수 없다.
        if self.machine.recording and self.machine.event is not None:
            since = self.segments.pre_roll_start(
                self.machine.event.detected_at, int(self._param('pre_seconds'))
            )
            self._collect(self.segments.segments_covering(since))

        transition = self.machine.tick(now)
        if transition is None:
            return

        self.get_logger().info(transition)
        self._publish_status(transition)
        if self.machine.state.value == 'FINALIZING':
            self._finalize()

    def _finalize(self) -> None:
        event = self.machine.event
        if event is None or self.work_directory is None or self.media_id is None:
            self.machine.finish(False)
            return

        segments = [self.collected[key] for key in sorted(self.collected)]
        end_reason = event.end_reason.value if event.end_reason else 'NORMAL'

        # 상한을 먼저 확인한다. 만들고 나서 지우면 디스크를 한 번 더 쓴다.
        estimated = sum(
            (segment.duration_ms / 1000)
            * int(self._param('encoder_bitrate_kbps'))
            * 1000
            / 8
            for segment in segments
        )
        verdict = self.pending.enforce_cap(int(estimated))
        if verdict['removed']:
            self.get_logger().warn(
                f"pending 상한 확보: {verdict['freedBytes'] / 1e6:.1f}MB 삭제 "
                f"({len(verdict['removed'])}건)"
            )

        if not verdict['admitted']:
            self.get_logger().error(
                f"pending 상한 초과로 영상을 포기한다. "
                f"사용 {verdict['usedBytes'] / 1e6:.0f}MB / "
                f"상한 {verdict['capBytes'] / 1e6:.0f}MB. "
                '썸네일과 보고서는 남긴다(32-5).'
            )
            # 영상은 포기하지만 썸네일은 조각에서 뽑는다. 32-5가 이 경우에도
            # "썸네일과 JSON 보고서는 남긴다"고 정했다. 조각은 아직 작업 디렉터리에
            # 있고(cleanup_segments는 아래에서 부른다) 썸네일은 수십 KB라 상한에
            # 실질적 영향이 없다.
            thumbnail = self._thumbnail_from_segments(segments)
            self._write_minimal_report(event, end_reason, thumbnail=thumbnail)
            self.pending.mark_disk_full(
                self.work_directory, '미업로드분만으로 상한을 넘었다'
            )
            self.pending.cleanup_segments(self.work_directory)
            self._publish_status(self.machine.finish(False))
            self._reset_event(event.encounter_id)
            return

        try:
            result = self.finalizer.finalize(
                segments,
                self.work_directory,
                encounter_id=event.encounter_id,
                media_id=self.media_id,
                detected_at=event.detected_at,
                end_reason=end_reason,
                person_count=event.person_count,
                mission_id=event.mission_id,
            )
        except FinalizeError as error:
            self.get_logger().error(f'MP4 생성 실패: {error.reason} / {error.detail}')
            self._write_minimal_report(
                event,
                end_reason,
                failure=error.reason,
                thumbnail=self._thumbnail_from_segments(segments),
            )
            self.pending.cleanup_segments(self.work_directory)
            self._publish_status(self.machine.finish(False))
            self._reset_event(event.encounter_id)
            return
        except (OSError, subprocess.TimeoutExpired) as error:
            self.get_logger().error(f'MP4 생성 중 예외: {error}')
            self._write_minimal_report(
                event,
                end_reason,
                failure='UNEXPECTED',
                thumbnail=self._thumbnail_from_segments(segments),
            )
            self.pending.cleanup_segments(self.work_directory)
            self._publish_status(self.machine.finish(False))
            self._reset_event(event.encounter_id)
            return

        # 조각은 hard link이므로 지워도 링 버퍼 원본에 영향이 없다. 남기면
        # 이벤트마다 MP4의 두 배를 차지한다.
        self.pending.cleanup_segments(self.work_directory)
        self._mark_upload_pending(result)

        self.get_logger().info(
            f'이벤트 저장 완료: {result.media_path.name} '
            f'{result.size_bytes / 1e6:.2f}MB {result.duration_seconds:.1f}초 '
            f'{result.frame_count}프레임 조각 {result.continuity["segmentCount"]}개'
        )
        self._publish_status(self.machine.finish(True))
        self._reset_event(event.encounter_id)

    def _mark_upload_pending(self, result) -> None:
        """보고서에 업로드 상태를 넣는다. S15P11A301-124가 이 값을 바꾼다."""
        if self.work_directory is None:
            return
        report_path = self.work_directory / REPORT_NAME
        try:
            report = read_report(report_path)
        except (OSError, json.JSONDecodeError):
            return
        report['uploadState'] = UPLOAD_STATE_PENDING
        report['mediaState'] = 'LOCAL'
        report['finalizedAt'] = format_utc(self._now())
        write_report(report_path, report)

    def _thumbnail_from_segments(self, segments: list[Segment]) -> Path | None:
        """MP4 없이 조각에서 썸네일을 뽑는다 (32-5).

        사전 구간이 끝난 지점의 조각을 쓴다. 첫 조각은 사람이 확정되기 전이라 빈
        복도일 수 있고, 확정 시점이 썸네일로 더 쓸모 있다(EventFinalizer가 MP4에서
        `pre_seconds` 지점을 쓰는 것과 같은 이유다).

        실패는 삼킨다. 썸네일이 없는 것보다 보고서까지 못 쓰는 것이 나쁘다.
        """
        if not segments or self.work_directory is None:
            return None
        index = min(int(self._param('pre_seconds')), len(segments) - 1)
        source = self.work_directory / segments[index].local_filename
        if not source.exists():
            return None
        try:
            # 조각 처음에서 뽑는다. TS는 입력 탐색이 부정확해 1초 조각에서 `-ss`를
            # 주면 오류 없이 빈 파일이 나온다(make_thumbnail 참고).
            return self.finalizer.make_thumbnail(
                source,
                self.work_directory,
                float(self._param('segment_seconds')),
                offset_seconds=0.0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self.get_logger().warn(f'조각에서 썸네일 생성 실패: {error}')
            return None

    def _write_minimal_report(
        self,
        event,
        end_reason: str,
        failure: str | None = None,
        thumbnail: Path | None = None,
    ) -> None:
        """영상이 없어도 사실은 남긴다 (32-5).

        관제에서 "이 시각에 사람을 발견했으나 영상이 없다"를 볼 수 있어야 한다.
        보고서를 안 쓰면 그 사실 자체가 사라진다.
        """
        if self.work_directory is None:
            return
        report_path = self.work_directory / REPORT_NAME
        report = {
            'schemaVersion': '1.0',
            'encounterId': event.encounter_id,
            'mediaId': self.media_id,
            'missionId': event.mission_id,
            'detectedAt': format_utc(event.detected_at),
            'endReason': end_reason,
            'personCount': event.person_count,
            'media': {
                'path': None,
                'sha256': None,
                'sizeBytes': 0,
                'thumbnail': thumbnail.name if thumbnail else None,
            },
            'uploadState': UPLOAD_STATE_PENDING,
            'finalizedAt': format_utc(self._now()),
        }
        if failure:
            report['mediaState'] = f'RECORDING_FAILED_{failure}'
        write_report(report_path, report)

    def _reset_event(self, encounter_id: str | None = None) -> None:
        """이벤트 상태를 비우고 mission_manager에 마감을 알린다.

        `REPORT_COMMITTED`를 여기서 내는 이유는 마감 경로가 넷이기 때문이다.
        정상 완료, 디스크 상한 초과, MP4 생성 실패, 예상 밖 예외. 각 경로에
        따로 넣으면 하나를 빠뜨리고, 빠뜨린 경로에서는 `REPORTING`에 갇혀 다음
        사람을 찾지 못한다(S15P11A301-139).

        영상이 없어도 발행한다. 32-5가 공간 부족·실패 시에도 "썸네일과 JSON
        보고서는 남긴다"고 정했고, 그것이 로컬 보고 저장 완료다. 영상 유무로
        신호를 나누면 실패한 이벤트에서 임무가 멈춘다.
        """
        if encounter_id:
            self._publish_report_committed(encounter_id)
        self.work_directory = None
        self.collected = {}
        self.media_id = None

    def _publish_report_committed(self, encounter_id: str) -> None:
        """26.3의 REPORTING → EXPLORING 을 일으킨다.

        26.1이 "각 노드는 자신이 관찰한 사실만 알린다"고 정했다. 이 노드는 "영상과
        보고서를 로컬에 저장했다"는 사실만 알리고, 그것으로 어떤 상태로 갈지는
        mission_manager가 결정한다. 그래서 목표 상태를 적지 않는다.
        """
        body = {
            'signal': 'REPORT_COMMITTED',
            'sentAt': format_utc(self._now()),
            'source': 'PERCEPTION',
            'encounterId': encounter_id,
            'detail': '이벤트 보고서를 로컬에 저장했다',
            'commandId': None,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.signal_pub.publish(message)
        self.get_logger().info(
            f'REPORT_COMMITTED 발행 {encounter_id[:8]} — mission_manager가 '
            '탐사를 재개한다'
        )

    def _publish_status(self, transition: str) -> None:
        message = String()
        message.data = json.dumps(
            {
                'state': self.machine.state.value,
                'transition': transition,
                'encounterId': (
                    self.machine.event.encounter_id if self.machine.event else None
                ),
                'collectedSegments': len(self.collected),
                'at': format_utc(self._now()),
            },
            ensure_ascii=False,
        )
        self.status_pub.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RecordingManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

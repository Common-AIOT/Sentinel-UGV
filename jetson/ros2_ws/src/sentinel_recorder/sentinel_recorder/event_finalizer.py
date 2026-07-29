"""이벤트 MP4 생성 (S15P11A301-123, 명세 32-5 「이벤트 종료와 MP4 생성」).

명세가 순서를 정해 뒀고 그대로 따른다.

    조각 목록 시각·PTS 순서 검증
    → 누락 조각 검사
    → H.264 재다중화
    → event.partial.mp4 생성
    → 재생 검사
    → SHA-256 계산
    → event.mp4 원자적 rename
    → thumbnail.jpg 생성
    → UPLOAD_PENDING 등록

## `.partial`을 쓰는 이유

전원이 차단되면 반쪽 MP4가 남는다. 최종 이름을 처음부터 쓰면 그것이 정상 파일인지
알 수 없다. `.partial`로 만들고 검사를 통과한 뒤에만 이름을 바꾸므로, 부팅 후
`.partial`이 보이면 그것은 실패한 것이다(32-5).

`os.replace`는 같은 볼륨에서 원자적이다. 검사를 통과한 파일만 최종 이름을 갖는다.

## 재생 검사를 하는 이유

`ffmpeg -c copy`가 성공해도 재생이 안 되는 경우가 있다. 조각 경계에서 PTS가
튀거나 첫 조각이 키프레임이 아니면 디코더가 시작하지 못한다. 파일 크기만 보고
성공으로 처리하면 업로드 후에야 알게 된다. 그래서 실제로 패킷을 읽어 확인한다.

## 오디오

32-5가 "H.264/AAC 재다중화"를 정했고 링 writer가 오디오 브랜치를 가진다
(S15P11A301-131). 조각에 AAC가 들어 있으면 concat 재다중화가 그대로 유지한다.

**오디오가 없어도 실패로 보지 않는다.** 마이크가 없거나 열리지 않으면 링 writer가
비디오만 기록하고 계속한다. 보고서의 `media.audio`가 null이면 그 경우다. 오디오
준비 실패로 이벤트를 잃는 것이 더 나쁘다.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .segment_store import Segment, continuity_report, format_utc

FFMPEG = 'ffmpeg'
FFPROBE = 'ffprobe'

PARTIAL_SUFFIX = '.partial.mp4'
FINAL_NAME = 'event.mp4'
THUMBNAIL_NAME = 'thumbnail.jpg'
REPORT_NAME = 'report.json'
CONCAT_NAME = 'segments.txt'

# 재다중화와 검사에 주는 시간 제한. 이벤트가 5분(MAX_DURATION)이므로 스트림
# 복사는 몇 초면 끝난다. 이보다 오래 걸리면 무언가 잘못된 것이고, 무한정
# 기다리면 녹화 노드가 다음 이벤트를 받지 못한다.
REMUX_TIMEOUT_SECONDS = 120
PROBE_TIMEOUT_SECONDS = 60


class FinalizeError(RuntimeError):
    """마무리 실패. 사유를 담아 RECORDING_FAILED로 기록한다."""

    def __init__(self, reason: str, detail: str = '') -> None:
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class FinalizeResult:
    media_path: Path
    thumbnail_path: Path | None
    sha256: str
    size_bytes: int
    duration_seconds: float | None
    frame_count: int
    continuity: dict[str, Any]


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_report(path: Path, report: dict[str, Any]) -> None:
    """`report.json`을 원자적으로 쓴다.

    `write_text`로 쓰면 안 된다. 두 가지 이유가 있다.

    첫째, `recording_manager`와 `media_uploader`는 별 프로세스이고 같은 파일을
    쓴다(전자는 상태 전이·복구, 후자는 `uploadState`). 한쪽이 쓰는 중에 다른 쪽이
    읽으면 잘린 JSON을 본다. 실제로 그렇게 됐고, `UploadWorker`가 그 이벤트를
    `REPORT_UNREADABLE`로 영구 실패 처리해 다시는 올리지 않았다.

    둘째, 32-5는 공간이 부족해도 "썸네일과 JSON 보고서는 남긴다"고 정했다. 쓰기
    도중에 전원이 끊겨 보고서가 손상되면 그 이벤트는 무엇이었는지조차 알 수 없다.
    MP4는 이미 `.partial` + `os.replace`로 이 문제를 막고 있는데 보고서만 빠져
    있었다.

    `fsync`까지 한다. `os.replace`는 원자적이지만 데이터가 디스크에 닿았다는 보장은
    아니다. microSD에서 전원이 끊기면 이름만 바뀌고 내용이 0바이트일 수 있다.
    """
    temporary = path.with_suffix(path.suffix + '.tmp')
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    with temporary.open('w', encoding='utf-8') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_report(path: Path) -> dict[str, Any]:
    """`report.json`을 읽는다. 실패는 호출자가 판단하도록 예외를 그대로 올린다."""
    return json.loads(path.read_text(encoding='utf-8'))


def write_concat_list(segments: list[Segment], directory: Path) -> Path:
    """ffmpeg concat demuxer 입력 파일.

    `-safe 0`이 필요 없도록 파일명만 적고 작업 디렉터리를 맞춘다. 경로에 공백이나
    작은따옴표가 있으면 concat 형식이 깨지므로, 조각 파일명이 `seg_%06d.ts`로
    고정된 것에 의존한다.
    """
    listing = directory / CONCAT_NAME
    # 이벤트 디렉터리의 이름(sequence 기준)을 쓴다. 링 버퍼의 파일명은 재사용되므로
    # 그것으로 목록을 만들면 같은 파일이 여러 번 나열되고, 이어붙인 MP4가 같은
    # 구간을 반복한다(Segment.local_filename 참고).
    lines = [f"file '{segment.local_filename}'" for segment in segments]
    listing.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return listing


def probe_audio_stream(path: Path) -> dict[str, Any] | None:
    """오디오 스트림 정보를 읽는다. 없으면 None.

    오디오가 없다고 실패로 보지 않는다. 마이크가 없는 기기에서도 비디오만으로
    이벤트가 성립해야 하고(S15P11A301-131), 32-5는 공간 부족 시에도 남길 것을
    정했을 뿐 오디오를 필수로 두지 않았다.

    `-count_packets`는 쓰지 않는다. 오디오 패킷은 5분에 수만 개여서 세는 비용이
    비디오보다 크고, 여기서 필요한 것은 "트랙이 있고 코덱이 무엇인가"다.
    """
    result = _run(
        [
            FFPROBE, '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_name,sample_rate,channels,duration',
            '-of', 'json',
            str(path),
        ],
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get('streams') or []
    except json.JSONDecodeError:
        return None
    if not streams:
        return None
    stream = streams[0]
    return {
        'codec': stream.get('codec_name'),
        'sampleRate': int(stream['sample_rate']) if stream.get('sample_rate') else None,
        'channels': stream.get('channels'),
        'durationSeconds': (
            round(float(stream['duration']), 3) if stream.get('duration') else None
        ),
    }


def probe_playable(path: Path) -> tuple[int, float | None]:
    """실제로 패킷을 읽어 재생 가능한지 확인한다.

    `-count_packets`를 쓴다. `-count_frames`는 전부 디코딩하므로 5분 영상에서
    수십 초가 걸린다(S15P11A301-62에서 18000프레임 디코딩으로 timeout에 걸렸다).
    `alignment=au`이므로 패킷 하나가 프레임 하나다.
    """
    result = _run(
        [
            FFPROBE, '-v', 'error',
            '-select_streams', 'v:0',
            '-count_packets',
            '-show_entries', 'stream=nb_read_packets,codec_name',
            '-show_entries', 'format=duration',
            '-of', 'json',
            str(path),
        ],
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise FinalizeError('PLAYBACK_CHECK_FAILED', result.stderr.strip()[:300])

    try:
        payload = json.loads(result.stdout)
        stream = payload['streams'][0]
        frames = int(stream['nb_read_packets'])
        codec = stream.get('codec_name')
        duration_raw = payload.get('format', {}).get('duration')
        duration = float(duration_raw) if duration_raw else None
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as error:
        raise FinalizeError('PLAYBACK_CHECK_FAILED', f'ffprobe 출력 해석 실패: {error}')

    if codec != 'h264':
        raise FinalizeError('PLAYBACK_CHECK_FAILED', f'예상과 다른 코덱: {codec}')
    if frames <= 0:
        raise FinalizeError('PLAYBACK_CHECK_FAILED', '읽을 수 있는 패킷이 없다')
    return frames, duration


class EventFinalizer:
    """조각 목록을 MP4로 만든다. ROS를 모르므로 단독으로 시험할 수 있다."""

    def __init__(
        self,
        segment_seconds: int = 1,
        thumbnail_offset_seconds: float = 3.0,
        min_continuity_ratio: float = 0.8,
        logger: Any | None = None,
    ) -> None:
        # ROS 로거를 받지만 없어도 된다. 이 클래스는 ROS를 모르는 상태로
        # 단독 시험할 수 있어야 한다.
        self._logger = logger
        self.segment_seconds = segment_seconds
        # 썸네일을 사전 영상 길이만큼 들어간 지점에서 뽑는다. 파일 첫 프레임은
        # 사람이 확정되기 전이라 빈 복도일 수 있다. 확정 시점이 썸네일로 더 쓸모
        # 있다.
        self.thumbnail_offset_seconds = thumbnail_offset_seconds
        self.min_continuity_ratio = min_continuity_ratio

    def finalize(
        self,
        segments: list[Segment],
        work_directory: Path,
        *,
        encounter_id: str,
        media_id: str,
        detected_at: datetime,
        end_reason: str,
        person_count: int,
        mission_id: str | None = None,
    ) -> FinalizeResult:
        if not segments:
            raise FinalizeError('NO_SEGMENTS', '수집된 조각이 없다')

        work_directory.mkdir(parents=True, exist_ok=True)

        # 1. 시각·PTS 순서와 누락 검사
        continuity = continuity_report(segments, self.segment_seconds)
        if continuity['missingSequences']:
            raise FinalizeError(
                'SEGMENTS_MISSING',
                f"누락 sequence {continuity['missingSequences'][:10]}",
            )
        if continuity['ptsRegressions']:
            raise FinalizeError(
                'PTS_REGRESSION',
                f"PTS 역행 sequence {continuity['ptsRegressions'][:10]}",
            )
        ratio = continuity['durationRatio']
        if ratio is not None and ratio < self.min_continuity_ratio:
            # 조각 수는 맞는데 총 길이가 크게 짧다. 조각이 잘렸다는 뜻이다.
            raise FinalizeError(
                'DURATION_SHORTFALL', f'길이 비율 {ratio:.2f}'
            )

        # 2. 재다중화. 스트림 복사이므로 재인코딩하지 않는다.
        partial = work_directory / f'{media_id}{PARTIAL_SUFFIX}'
        listing = write_concat_list(segments, work_directory)
        remux = _run(
            [
                FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'concat', '-safe', '0',
                '-i', str(listing),
                '-c', 'copy',
                # faststart는 moov를 앞으로 옮겨 브라우저가 전체를 받기 전에
                # 재생을 시작할 수 있게 한다. 다시보기에서 체감이 크다.
                '-movflags', '+faststart',
                # TS의 PTS는 1시간 오프셋에서 시작한다. 그대로 두면 MP4의
                # 시작 시각이 1시간이 되어 플레이어가 앞부분을 비운다.
                '-reset_timestamps', '1',
                str(partial),
            ],
            timeout=REMUX_TIMEOUT_SECONDS,
        )
        if remux.returncode != 0 or not partial.exists():
            raise FinalizeError('REMUX_FAILED', remux.stderr.strip()[:300])

        # 3. 재생 검사
        frames, duration = probe_playable(partial)

        #    오디오가 재다중화를 넘어왔는지 확인한다(S15P11A301-131).
        #
        #    조각에 AAC가 있는데 MP4에 없으면 `-c copy`가 트랙을 잃은 것이다.
        #    그 경우 로그만 남기고 이벤트는 살린다. 소리가 빠진 영상은 여전히
        #    재생되고 사람이 찍혀 있으며, 5분 영상을 통째로 버리는 것이 소리를
        #    잃는 것보다 나쁘다.
        #
        #    조각에 오디오가 없으면(마이크 없는 기기) 검사할 것도 없다.
        #    `segment.path`는 index.json의 상대 경로다. 실제로 여기 있는 파일은
        #    hard link한 사본이고 이름이 `local_filename`이다.
        first_segment = work_directory / segments[0].local_filename
        audio_expected = probe_audio_stream(first_segment) is not None
        audio_dropped = audio_expected and probe_audio_stream(partial) is None
        if audio_dropped and self._logger is not None:
            self._logger.error(
                '조각에는 오디오가 있는데 재다중화 결과에 없다. 소리 없이 '
                '이벤트를 남긴다. ffmpeg concat이 트랙을 잃었는지 확인해야 한다.'
            )

        # 4. SHA-256
        checksum = sha256_of(partial)
        size_bytes = partial.stat().st_size

        # 5. 원자적 rename. 여기까지 통과한 파일만 최종 이름을 갖는다.
        final = work_directory / FINAL_NAME
        os.replace(partial, final)

        # 6. 썸네일. 실패해도 이벤트를 실패로 만들지 않는다. 32-5가 공간 확보
        #    실패 시에도 "썸네일과 JSON 보고서는 남긴다"고 한 것처럼, 영상과
        #    썸네일은 독립적으로 다룬다.
        thumbnail = self.make_thumbnail(final, work_directory, duration)

        report = {
            'schemaVersion': '1.0',
            'encounterId': encounter_id,
            'mediaId': media_id,
            'missionId': mission_id,
            'detectedAt': format_utc(detected_at),
            'endReason': end_reason,
            'personCount': person_count,
            'media': {
                'path': FINAL_NAME,
                'sha256': checksum,
                'sizeBytes': size_bytes,
                'durationSeconds': duration,
                'frameCount': frames,
                'thumbnail': THUMBNAIL_NAME if thumbnail else None,
                # 오디오는 없을 수 있다. 마이크가 없거나 열리지 않으면 링 writer가
                # 비디오만 기록한다(S15P11A301-131). null이면 "소리 없는 영상"이고
                # 실패가 아니다.
                'audio': probe_audio_stream(final),
                # 조각에 소리가 있었는데 MP4에서 사라졌으면 True. 마이크가 없어
                # 처음부터 소리가 없던 경우와 구분한다. 둘 다 audio는 null이지만
                # 전자는 결함이고 후자는 정상이다.
                'audioDropped': audio_dropped,
            },
            'segments': {
                'count': continuity['segmentCount'],
                'firstSequence': segments[0].sequence,
                'lastSequence': segments[-1].sequence,
                'firstSegmentIsKeyframe': continuity['firstSegmentIsKeyframe'],
                'totalDurationMs': continuity['totalDurationMs'],
                'firstStartedAt': format_utc(segments[0].started_at),
                'lastEndedAt': format_utc(segments[-1].ended_at),
            },
            # VID-03과 VID-04를 산출물만 보고 판정할 수 있게 넣는다.
            #
            # 로그 문구로 판정하면 안 된다. 이벤트 시작 시점의 index.json에는
            # 아직 열려 있는 조각이 없어서 초기 수집이 최대 1조각 짧게 잡히고,
            # 그 조각은 닫히는 즉시 따라붙는다. 즉 로그의 "사전 N초"는 과소
            # 보고이며 최종 파일과 다르다.
            #
            # 실제 사전 영상 길이는 첫 조각 시작과 detectedAt의 차이다.
            'coverage': {
                'preRollSeconds': round(
                    (detected_at - segments[0].started_at).total_seconds(), 3
                ),
                'postRollSeconds': round(
                    (segments[-1].ended_at - detected_at).total_seconds(), 3
                ),
            },
        }
        write_report(work_directory / REPORT_NAME, report)

        return FinalizeResult(
            media_path=final,
            thumbnail_path=thumbnail,
            sha256=checksum,
            size_bytes=size_bytes,
            duration_seconds=duration,
            frame_count=frames,
            continuity=continuity,
        )

    def make_thumbnail(
        self,
        media: Path,
        directory: Path,
        duration: float | None,
        offset_seconds: float | None = None,
    ) -> Path | None:
        """미디어 한 개에서 정지 프레임을 뽑는다.

        MP4뿐 아니라 링 버퍼의 `.ts` 조각도 입력으로 받는다. 상한 초과로 MP4를
        포기했을 때 조각에서 직접 뽑아야 하기 때문이다. 32-5는 그 경우에도
        "썸네일과 JSON 보고서는 남긴다"고 정했고, 관제가 그 자리에 무슨 일이
        있었는지 볼 유일한 시각 증거가 이것이다.

        `offset_seconds=0`을 주면 파일 처음에서 뽑는다. **MPEG-TS 조각에는 이것이
        필요하다.** TS에는 정확한 duration 헤더가 없어 입력 탐색이 부정확하고,
        1초짜리 조각에서 `-ss 0.5`는 오류 메시지 없이 빈 파일을 만든다. 조각 하나가
        1초이므로 그 안에서 어디를 뽑든 같은 순간이다.
        """
        offset = (
            self.thumbnail_offset_seconds
            if offset_seconds is None
            else offset_seconds
        )
        if offset_seconds is None and duration is not None and duration <= offset:
            # 짧은 이벤트는 중간 지점에서 뽑는다. 오프셋이 길이를 넘으면 ffmpeg가
            # 프레임을 못 찾아 빈 파일을 만든다.
            offset = max(0.0, duration / 2)

        target = directory / THUMBNAIL_NAME
        result = _run(
            [
                FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
                '-ss', f'{offset:.3f}',
                '-i', str(media),
                '-frames:v', '1',
                '-q:v', '3',
                str(target),
            ],
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            return None
        return target

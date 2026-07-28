"""1초 조각 링 버퍼와 조각 인덱스 (S15P11A301-123, 명세 32-5).

인코딩된 H.264를 1초 MPEG-TS 조각으로 지속 기록하고 최근 N개를 순환 보관한다.
MPEG-TS를 쓰는 이유는 조각 단위 복구가 쉽고 이벤트 종료 후 MP4로 재다중화할 수
있기 때문이다.

이 모듈은 파이프라인 프로세스 안에서 돈다. `splitmuxsink`가 tee의 녹화 분기에
달리기 때문이다. 반면 **녹화 상태 머신과 MP4 생성은 별 프로세스**(`sentinel_recorder`)
에 둔다. 완료 조건이 "녹화를 인위적으로 실패시켜도 관제 스트리밍이 유지된다"이고,
같은 프로세스면 MP4 생성 실패가 파이프라인 재구성을 유발해 스트리밍까지 끊긴다.
`index.json`이 두 프로세스의 경계다.

## index.json을 원자적으로 쓰는 이유

녹화 노드가 이 파일을 읽는 동안 우리가 덮어쓰면 반쪽 JSON을 보게 된다. 1초마다
쓰므로 확률이 낮지 않다. 임시 파일에 쓰고 `os.replace`로 바꾼다.

## 조각이 삭제돼도 이벤트가 안전한 이유

`max-files`가 오래된 조각을 지운다. 녹화 노드는 조각을 **hard link**로 가져가므로
링 버퍼가 원본을 지워도 inode가 살아 있다. 복사가 끝나기를 기다릴 필요가 없고
디스크를 두 번 쓰지도 않는다. 명세 32-5가 hard link를 명시한 이유다.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INDEX_FILENAME = 'index.json'
SEGMENT_PREFIX = 'seg_'
SEGMENT_SUFFIX = '.ts'


def _utc_iso(epoch_seconds: float) -> str:
    """32-5 메타데이터의 시각 형식. UTC만 쓰고 Z로 끝난다."""
    stamp = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return stamp.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def segment_filename(segment_id: int) -> str:
    return f'{SEGMENT_PREFIX}{segment_id:06d}{SEGMENT_SUFFIX}'


class SegmentIndex:
    """조각 메타데이터를 모아 index.json으로 내보낸다.

    32-5가 정한 필드를 그대로 쓴다.

        segmentId, startedAt, endedAt, durationMs, firstPts, firstFrameKey, path

    여기에 `firstMonotonicNs`를 더한다. 32-5 「시각 기준」이 PTS와 monotonic,
    wall time 세 가지를 함께 기록하라고 요구하기 때문이다. `usb_cam`의
    `header.stamp`는 노드 시작 시점에 한 번 계산한 고정 오프셋을 쓰므로 진짜
    wall clock이 아니고 NTP 보정을 따라가지 않는다. 이벤트와 조각을 wall time으로
    매칭하면 그 오차가 그대로 들어온다.
    """

    def __init__(self, directory: Path, keep: int) -> None:
        self.directory = directory
        self.keep = keep
        self._segments: list[dict[str, Any]] = []
        self._open: dict[str, Any] | None = None
        # splitmuxsink의 fragment_id는 max-files 때문에 되돌아간다. 순서를
        # 판정할 단조 증가 값이 따로 필요하다.
        self._sequence = 0

    def open_segment(
        self,
        segment_id: int,
        first_pts_ns: int | None,
        first_frame_key: bool,
        muxed_pts_ns: int | None = None,
    ) -> None:
        """새 조각이 시작됐다. 이전 조각은 이 시점에 끝난 것으로 닫는다.

        `first_pts_ns`는 appsrc에 밀어넣은 입력 PTS다(32-5가 말하는 스트림 PTS).
        `muxed_pts_ns`는 mpegtsmux를 지난 값이며 1시간 오프셋이 붙어 있다. 둘을
        같이 남기는 이유는 재생 문제를 조사할 때 TS 타임스탬프가 필요하기
        때문이다.
        """
        now = time.time()
        self._close_open(now)

        # segmentId를 순번(sequence)과 분리한다. splitmuxsink의 fragment_id는
        # max-files 때문에 0으로 되돌아가므로 단독으로는 순서를 나타내지 못한다.
        # 파일명은 fragment_id를 쓰되, 순서 판정은 sequence로 한다.
        self._sequence += 1

        self._open = {
            'segmentId': segment_id,
            'sequence': self._sequence,
            'startedAt': _utc_iso(now),
            'endedAt': None,
            'durationMs': None,
            'firstPts': first_pts_ns,
            'muxedPts': muxed_pts_ns,
            'firstFrameKey': first_frame_key,
            'firstMonotonicNs': time.monotonic_ns(),
            'path': f'buffer/{segment_filename(segment_id)}',
            '_startedEpoch': now,
        }

    def _close_open(self, ended_epoch: float) -> None:
        if self._open is None:
            return
        started = self._open.pop('_startedEpoch')
        self._open['endedAt'] = _utc_iso(ended_epoch)
        self._open['durationMs'] = int(round((ended_epoch - started) * 1000))
        self._segments.append(self._open)
        self._open = None

        # 링 버퍼가 파일을 지우므로 인덱스도 같은 길이로 자른다. 인덱스에만 남으면
        # 녹화 노드가 없는 파일을 찾는다.
        if len(self._segments) > self.keep:
            self._segments = self._segments[-self.keep:]

    def close(self) -> None:
        """종료 시 열린 조각을 닫는다."""
        self._close_open(time.time())

    def snapshot(self) -> dict[str, Any]:
        """열린 조각은 포함하지 않는다.

        아직 쓰이는 중인 파일을 녹화 노드가 가져가면 잘린 조각을 얻는다.
        완료된 조각만 노출하는 것이 32-5 「이벤트 시작」의 "완료 조각을 조회한다"에
        해당한다.
        """
        return {
            'schemaVersion': '1.0',
            'updatedAt': _utc_iso(time.time()),
            'segmentSeconds': None,
            'segments': list(self._segments),
        }

    def write(self, segment_seconds: int) -> None:
        """원자적으로 index.json을 갱신한다."""
        payload = self.snapshot()
        payload['segmentSeconds'] = segment_seconds
        target = self.directory / INDEX_FILENAME
        temporary = self.directory / f'{INDEX_FILENAME}.tmp'
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        os.replace(temporary, target)


class RingBufferWriter:
    """`splitmuxsink`를 붙이고 조각 메타데이터를 기록한다.

    GStreamer 요소를 직접 만들지 않고 파이프라인 문자열을 돌려준다. 노드가
    `Gst.parse_launch`로 전체를 세우는 구조를 유지하는 편이 재구성 로직과
    어긋나지 않는다.
    """

    def __init__(
        self,
        directory: str | Path,
        segment_seconds: int,
        ring_segments: int,
        send_keyframe_requests: bool = False,
        logger: Any | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.segment_seconds = max(1, int(segment_seconds))
        self.ring_segments = max(2, int(ring_segments))
        self.send_keyframe_requests = bool(send_keyframe_requests)
        self._logger = logger
        self.index = SegmentIndex(self.directory, self.ring_segments)
        self._sink = None

        # 마지막으로 appsrc에 밀어넣은 입력 PTS.
        #
        # `format-location-full`이 주는 샘플은 mpegtsmux를 지난 것이라 PTS에
        # 1시간(3600초) 오프셋이 붙어 있다. TS는 음수 PTS를 피하려고 기준을
        # 옮긴다. 그 값을 그대로 쓰면 32-5가 말하는 "스트림 PTS"가 아니고,
        # 리베이스 전후로 기준이 달라져 조각 연속성 검사가 무의미해진다.
        #
        # 그래서 노드가 프레임을 밀 때마다 입력 PTS를 여기 적어 둔다. 조각
        # 경계에서 최신 값을 읽으므로 한 프레임(약 33ms) 오차가 있다. 3초 사전
        # 영상을 찾는 용도에는 충분하다.
        self._last_input_pts_ns: int | None = None

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def sink_description(self, queue_buffers: int) -> str:
        """tee의 녹화 분기에 붙일 요소 문자열.

        `leaky=no`는 32-5의 non-leaky 큐 정책이다. 프레임을 버리면 조각에 구멍이
        생기고 그것이 곧 조각 누락이다.

        `send-keyframe-requests`는 기본을 false로 둔다. true로 두면 splitmuxsink가
        `max-size-time`에 도달할 때 상류로 force-keyframe 이벤트를 보내고,
        `x264enc`가 즉시 IDR을 만들어 그 지점에서 또 한 번 쪼갠다. 이미 자연
        IDR에서 한 번 쪼갠 직후이므로 30ms짜리 조각이 하나 더 생긴다. 실측에서
        1001ms와 30ms가 번갈아 나왔다.

        인코더의 `key-int-max`가 이미 1초마다 IDR을 만들므로 요청이 필요 없다.
        조각 시작이 키프레임인지는 `firstFrameKey`로 확인하고 경고를 남긴다.
        """
        pattern = str(self.directory / f'{SEGMENT_PREFIX}%06d{SEGMENT_SUFFIX}')
        return (
            f'queue name=record_queue max-size-buffers={int(queue_buffers)} '
            f'leaky=no '
            f'! splitmuxsink name=ring '
            f'    muxer-factory=mpegtsmux '
            f'    location="{pattern}" '
            f'    max-size-time={self.segment_seconds * 1_000_000_000} '
            f'    max-size-bytes=0 '
            f'    max-files={self.ring_segments} '
            f'    send-keyframe-requests={str(self.send_keyframe_requests).lower()} '
            f'    async-finalize=true'
        )

    def note_input_pts(self, pts_ns: int) -> None:
        """appsrc에 프레임을 밀 때마다 호출한다. 조각 메타데이터의 기준이 된다."""
        self._last_input_pts_ns = int(pts_ns)

    def attach(self, pipeline) -> bool:
        """`format-location-full`을 연결한다.

        이 시그널만이 조각의 첫 샘플을 준다. PTS와 키프레임 여부를 여기서 읽지
        않으면 나중에 파일을 다시 파싱해야 한다.
        """
        self._sink = pipeline.get_by_name('ring')
        if self._sink is None:
            return False
        self._sink.connect('format-location-full', self._on_format_location)
        return True

    def _on_format_location(self, _sink, fragment_id: int, first_sample) -> str:
        first_frame_key = False
        muxed_pts_ns: int | None = None

        # 첫 샘플이 없을 수 있다. 그때는 메타데이터를 비우고 계속한다. 조각 기록을
        # 멈추는 것보다 낫다.
        if first_sample is not None:
            buffer = first_sample.get_buffer()
            if buffer is not None:
                if buffer.pts != 0xFFFFFFFFFFFFFFFF:  # Gst.CLOCK_TIME_NONE
                    muxed_pts_ns = int(buffer.pts)
                # DELTA_UNIT 플래그가 없으면 키프레임이다.
                from gi.repository import Gst  # 지역 import로 모듈 의존을 좁힌다

                first_frame_key = not bool(
                    buffer.mini_object.flags & Gst.BufferFlags.DELTA_UNIT
                )

        self.index.open_segment(
            fragment_id, self._last_input_pts_ns, first_frame_key, muxed_pts_ns
        )
        self.index.write(self.segment_seconds)

        if self._logger is not None and not first_frame_key:
            # 조각 시작이 키프레임이 아니면 이벤트 첫 화면이 깨진다(32-5).
            self._logger.warn(
                f'조각 {fragment_id}의 첫 프레임이 키프레임이 아니다. '
                'encoder_key_int_max와 segment_seconds가 맞는지 확인한다.'
            )

        return str(self.directory / segment_filename(fragment_id))

    def split_now(self) -> bool:
        """진행 중인 조각을 즉시 마감한다.

        PTS 리베이스(카메라 재시작, 시각 점프) 때 호출한다. 시간 불연속을 조각
        경계로만 겪게 해야 재생 호환성 문제를 막는다(32-5, S15P11A301-62 계약).
        """
        if self._sink is None:
            return False
        self._sink.emit('split-now')
        return True

    def close(self) -> None:
        self.index.close()
        try:
            self.index.write(self.segment_seconds)
        except OSError as error:
            if self._logger is not None:
                self._logger.warn(f'index.json 마무리 실패: {error}')

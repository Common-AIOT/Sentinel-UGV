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

# 오디오 브랜치에 속한 요소 이름 조각. GStreamer가 오류 소스로 이 중 하나를 주면
# 노드가 오디오만 끄고 파이프라인을 다시 세운다(S15P11A301-131).
#
# 이 목록이 여기 있는 이유는 `audio_branch_description`이 요소 이름을 정하기
# 때문이다. 노드 쪽에 두면 브랜치를 고칠 때 한쪽만 바뀌고, 그러면 마이크 장애가
# 비디오 장애로 오분류돼 재구성이 무한 반복된다.
#
# 요소 이름 문자열로 판단하는 것이 거칠지만, 대안은 파이프라인에서 요소를 되짚어
# 브랜치 소속을 확인하는 것이고 그쪽이 더 깨지기 쉽다.
AUDIO_ELEMENT_HINTS = (
    'audio_queue', 'pulsesrc', 'alsasrc', 'pipewiresrc',
    'voaacenc', 'avenc_aac', 'opusenc',
    'audioconvert', 'audioresample', 'aacparse',
)


def is_audio_element(name: str) -> bool:
    """GStreamer 오류 소스가 오디오 브랜치의 요소인가.

    노드는 `message.src.get_name()`을 넘긴다. 이름 없는 요소에 GStreamer가
    자동으로 붙이는 형태가 `pulsesrc0`, `voaacenc0`이므로 부분 일치로 본다.
    실제 실패에서 확인한 값이다.

        ERROR: from element /GstPipeline:pipeline0/GstPulseSrc:pulsesrc0:
               Failed to connect stream: Invalid argument
    """
    return any(hint in name.lower() for hint in AUDIO_ELEMENT_HINTS)


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
        audio_enabled: bool = False,
        audio_source: str = 'pulsesrc',
        audio_encoder: str = 'voaacenc bitrate=64000',
        audio_rate: int = 48000,
        audio_channels: int = 1,
        audio_queue_seconds: int = 3,
        logger: Any | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.segment_seconds = max(1, int(segment_seconds))
        self.ring_segments = max(2, int(ring_segments))
        self.send_keyframe_requests = bool(send_keyframe_requests)
        # 32-5가 "H.264/AAC 재다중화"를 정했고 32-6이 대화 음성을 증빙으로 둔다.
        # 로봇이 묻고 요구조자가 답하는 대화가 통째로 들리는 것이 구조화 보고서만
        # 남기는 것보다 완전하다(S15P11A301-131).
        #
        # 소스와 인코더를 설정값으로 빼는 이유는 마이크가 확정되지 않았기
        # 때문이다. BRIO 100 내장 마이크가 잠정이며 STT 인식률 미달 시 USB 마이크로
        # 바꾼다(TBD-AUD-001).
        self.audio_enabled = bool(audio_enabled)
        self.audio_source = str(audio_source)
        self.audio_encoder = str(audio_encoder)
        self.audio_rate = int(audio_rate)
        self.audio_channels = int(audio_channels)
        self.audio_queue_seconds = max(1, int(audio_queue_seconds))
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
        video = (
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
        if not self.audio_enabled:
            return video
        return f'{video} {self.audio_branch_description()}'

    def audio_branch_description(self) -> str:
        """splitmuxsink의 audio_0 pad에 붙일 오디오 브랜치.

        비디오와 다른 클럭을 쓴다는 점이 이 브랜치의 유일한 실질 위험이다.

            비디오  appsrc do-timestamp=false, PTS = usb_cam stamp - 첫 stamp
            오디오  pulsesrc, 파이프라인 클럭

        둘 다 CLOCK_MONOTONIC 기반이지만 5분 이벤트(MAX_EVENT_SECONDS)에서
        드리프트가 쌓일 수 있다. 32-6이 "카메라와 마이크의 monotonic timestamp를
        기준으로 동기화한다"고 한 이유다. README의 검증 기록에 실측치를 남긴다.

        `queue`를 둔다. 오디오가 막히면 mpegtsmux가 비디오도 기다린다. leaky는
        쓰지 않는다 — 오디오를 버리면 그 구간이 무음이 되고, 대화 증빙에서 빠진
        구간은 복구할 수 없다.

        ## 큐를 시간으로 재는 이유

        `max-size-buffers`로 재면 안 된다. 버퍼 하나의 길이가 `pulsesrc`의
        `latency-time`에 달려 있어서, 그 값을 바꾸면 큐의 실제 용량이 같이
        바뀐다. 10ms일 때 64개는 0.64초지만 50ms로 올리면 3.2초가 된다.

        `max-size-buffers=0`으로 끄고 시간으로만 잰다. 그래야 소스 설정과
        무관하게 용량이 일정하다.

        ## 실측: 부하가 높으면 오디오가 사라진다

        `pulsesrc` 기본값(buffer-time 200ms)으로 5분 이벤트를 녹화하니 1초
        조각마다 오디오가 약 620ms만 담겼다. 38%가 사라졌고 로그에는
        `Can't record audio fast enough`가 반복됐다.

            videotestsrc만 (부하 낮음)         오디오/비디오 99.5%
            실제 경로 + YOLO 탐지 동시 구동    오디오/비디오 61.8%

        구조 문제가 아니라 스케줄링 문제다. `x264enc`(CPU 인코딩)와 YOLO가
        코어를 다 쓰면 `pulsesrc`의 읽기 스레드가 늦게 깨고, 그동안 PulseAudio
        쪽 링버퍼가 넘쳐 샘플이 버려진다. 큐를 키워도 소용없다 — 손실은 큐에
        닿기 전, 소스 안에서 일어난다.

        그래서 `audio_source`에 `buffer-time`과 `latency-time`을 준다(media.yaml).
        buffer-time을 키우면 넘치기까지의 여유가 늘고, latency-time을 키우면
        깨어나는 횟수가 줄어 늦은 깨어남에 덜 민감해진다.
        """
        return (
            f'{self.audio_source} '
            f'! queue name=audio_queue '
            f'    max-size-buffers=0 max-size-bytes=0 '
            f'    max-size-time={self.audio_queue_seconds * 1_000_000_000} '
            f'    leaky=no '
            f'! audioconvert ! audioresample '
            f'! audio/x-raw,rate={self.audio_rate},channels={self.audio_channels} '
            f'! {self.audio_encoder} ! aacparse ! ring.audio_0'
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

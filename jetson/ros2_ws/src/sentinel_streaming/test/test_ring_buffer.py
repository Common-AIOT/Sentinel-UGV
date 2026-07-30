"""링 버퍼 파이프라인 문자열 검증 (S15P11A301-131).

`ring_buffer`는 GStreamer 요소를 만들지 않고 문자열만 돌려준다. 그래서 `gi`도
`rclpy`도 없이 시험할 수 있고, CI에서 돌 수 있는 유일한 스트리밍 모듈이다.

여기서 검증하는 것은 **연결 구조**다. 실제 협상(마이크 열림, AAC 인코딩)은
장비가 필요하므로 README의 검증 기록으로 남긴다. 반대로 "audio_0 pad에 붙었는가",
"오디오를 끄면 브랜치가 사라지는가"는 문자열만 보면 알 수 있고, 이것을 틀리면
파이프라인이 아예 서지 않는다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_streaming.ring_buffer import (  # noqa: E402
    RingBufferWriter,
    SegmentIndex,
    segment_filename,
)


def writer(tmp_path, **kwargs) -> RingBufferWriter:
    return RingBufferWriter(tmp_path, segment_seconds=1, ring_segments=8, **kwargs)


# ----------------------------------------------------------------------
# 오디오를 끈 경우
# ----------------------------------------------------------------------


def test_audio_disabled_by_default(tmp_path):
    """기본값은 오디오 없음이다.

    기본을 켜면 마이크가 없는 개발 기기에서 파이프라인이 서지 않는다. 실제
    구성은 media.yaml이 켠다.
    """
    description = writer(tmp_path).sink_description(queue_buffers=16)
    assert 'ring.audio_0' not in description
    assert 'pulsesrc' not in description
    assert 'splitmuxsink name=ring' in description


def test_ring_uses_synchronous_finalize_for_wrapped_file_ids(tmp_path):
    """1초 순환 링은 muxer/sink를 조각마다 새로 만들지 않는다.

    GStreamer 1.20.3에서 async-finalize + max-files=8은 fragment id가 되감길 때
    아직 제거되지 않은 ``sink_8``과 이름이 충돌했다(S15P11A301-161). 실기기에서
    sequence 247 직후 RTSP와 링 녹화가 함께 멎었다.
    """
    description = writer(tmp_path).sink_description(queue_buffers=16)
    assert 'async-finalize=false' in description
    assert 'muxer=mpegtsmux' in description
    assert 'muxer-factory=' not in description
    assert 'max-files=8' in description


def test_audio_disabled_keeps_video_branch_intact(tmp_path):
    """오디오 유무가 비디오 브랜치를 바꾸지 않는다."""
    quiet = writer(tmp_path).sink_description(queue_buffers=16)
    loud = writer(tmp_path, audio_enabled=True).sink_description(queue_buffers=16)
    assert loud.startswith(quiet)


# ----------------------------------------------------------------------
# 오디오를 켠 경우
# ----------------------------------------------------------------------


def test_audio_branch_targets_splitmuxsink_audio_pad(tmp_path):
    """`ring.audio_0`으로 끝나야 한다.

    `ring.`만 쓰면 splitmuxsink가 비디오 pad를 요청하고, 이미 tee가 물려 있으므로
    연결에 실패한다. audio_0은 splitmuxsink가 정의한 요청 pad 이름이다.
    """
    description = writer(tmp_path, audio_enabled=True).audio_branch_description()
    assert description.endswith('ring.audio_0')
    assert 'pulsesrc' in description


def test_audio_branch_has_non_leaky_queue(tmp_path):
    """오디오 큐는 leaky여서는 안 된다.

    오디오를 버리면 그 구간이 무음이 되고 32-6의 대화 증빙에서 빠진 구간은 복구할
    수 없다. 큐 자체는 있어야 한다 — 없으면 오디오가 막힐 때 mpegtsmux가 비디오도
    기다린다.
    """
    description = writer(tmp_path, audio_enabled=True).audio_branch_description()
    assert 'queue name=audio_queue' in description
    assert 'leaky=no' in description
    assert 'leaky=downstream' not in description


def test_audio_branch_negotiates_configured_format(tmp_path):
    """caps에 설정한 rate/channels가 들어간다.

    BRIO 100 내장 마이크가 48000Hz 1채널이다. `audioconvert`와 `audioresample`을
    앞에 두므로 다른 마이크로 바꿔도 이 caps로 맞춰진다(TBD-AUD-001).
    """
    description = writer(
        tmp_path, audio_enabled=True, audio_rate=16000, audio_channels=2
    ).audio_branch_description()
    assert 'rate=16000' in description
    assert 'channels=2' in description
    assert 'audioconvert' in description
    assert 'audioresample' in description


def test_audio_branch_parses_encoder_output(tmp_path):
    """`aacparse`가 있어야 한다.

    mpegtsmux는 프레임 경계가 잡힌 AAC를 요구한다. voaacenc 출력을 직접 물리면
    "not-negotiated"로 파이프라인이 선 자리에서 죽는다.
    """
    description = writer(tmp_path, audio_enabled=True).audio_branch_description()
    assert 'aacparse' in description


def test_audio_source_and_encoder_are_configurable(tmp_path):
    """소스와 인코더를 바꿀 수 있다.

    마이크가 확정되지 않았다(TBD-AUD-001). ALSA로 내려가거나 인코더를 바꿔야 할 때
    코드를 고치지 않아도 되게 둔다.
    """
    description = writer(
        tmp_path,
        audio_enabled=True,
        audio_source='alsasrc device=hw:2',
        audio_encoder='avenc_aac bitrate=96000',
    ).audio_branch_description()
    assert 'alsasrc device=hw:2' in description
    assert 'avenc_aac bitrate=96000' in description
    assert 'pulsesrc' not in description


# ----------------------------------------------------------------------
# 조각 인덱스. 오디오가 붙어도 구조가 그대로여야 한다.
# ----------------------------------------------------------------------


def test_index_excludes_open_segment(tmp_path):
    """열린 조각은 노출하지 않는다 (32-5).

    쓰이는 중인 파일을 녹화 노드가 hard link하면 잘린 조각을 얻는다.
    """
    index = SegmentIndex(tmp_path, keep=8)
    index.open_segment(0, first_pts_ns=0, first_frame_key=True)
    assert index.snapshot()['segments'] == []

    index.open_segment(1, first_pts_ns=1_000_000_000, first_frame_key=True)
    segments = index.snapshot()['segments']
    assert len(segments) == 1
    assert segments[0]['segmentId'] == 0
    assert segments[0]['endedAt'] is not None
    assert segments[0]['durationMs'] is not None


def test_index_trims_to_ring_length(tmp_path):
    """링 길이를 넘으면 오래된 것을 버린다.

    파일은 `max-files`가 지운다. 인덱스에만 남으면 녹화 노드가 없는 파일을 찾는다.
    """
    index = SegmentIndex(tmp_path, keep=3)
    for segment_id in range(6):
        index.open_segment(segment_id, first_pts_ns=segment_id, first_frame_key=True)
    index.close()
    segments = index.snapshot()['segments']
    assert len(segments) == 3
    assert [segment['segmentId'] for segment in segments] == [3, 4, 5]


def test_sequence_survives_fragment_id_wraparound(tmp_path):
    """`sequence`는 단조 증가한다.

    splitmuxsink의 fragment_id는 `max-files` 때문에 0으로 되돌아간다. 그것으로
    순서를 판정하면 링이 한 바퀴 돈 뒤 이어붙인 MP4가 같은 구간을 반복한다.
    """
    index = SegmentIndex(tmp_path, keep=8)
    for fragment_id in (2, 0, 1):
        index.open_segment(fragment_id, first_pts_ns=0, first_frame_key=True)
    index.close()
    sequences = [segment['sequence'] for segment in index.snapshot()['segments']]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == 3


def test_index_written_atomically(tmp_path):
    """`.tmp`를 남기지 않는다. 1초마다 쓰므로 반쪽 JSON을 읽힐 확률이 낮지 않다."""
    index = SegmentIndex(tmp_path, keep=8)
    index.open_segment(0, first_pts_ns=0, first_frame_key=True)
    index.close()
    index.write(segment_seconds=1)
    assert (tmp_path / 'index.json').exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_ring_liveness_detects_stalled_fragment_rotation(tmp_path):
    """입력은 별도로 살아 있다고 확인된 상태에서 조각 정지만 판정한다."""
    ring = writer(tmp_path)
    assert ring.segment_age_seconds(now_monotonic=100.0) is None
    assert not ring.is_stalled(3.0, now_monotonic=100.0)

    ring.reset_liveness(now_monotonic=100.0)
    assert not ring.is_stalled(3.0, now_monotonic=102.999)
    assert ring.is_stalled(3.0, now_monotonic=103.0)


def test_ring_liveness_moves_with_each_new_fragment(tmp_path):
    """새 조각이 열리면 watchdog 기준도 앞으로 이동한다."""
    ring = writer(tmp_path)
    ring.reset_liveness(now_monotonic=100.0)
    ring.note_segment_opened(now_monotonic=102.0)

    assert ring.segment_age_seconds(now_monotonic=104.5) == pytest.approx(2.5)
    assert not ring.is_stalled(3.0, now_monotonic=104.999)
    assert ring.is_stalled(3.0, now_monotonic=105.0)


def test_ring_liveness_resets_after_pipeline_restart(tmp_path):
    """재구성 직후 이전 파이프라인의 오래된 시각으로 다시 재시작하지 않는다."""
    ring = writer(tmp_path)
    ring.reset_liveness(now_monotonic=100.0)
    ring.note_segment_opened(now_monotonic=101.0)
    assert ring.is_stalled(3.0, now_monotonic=104.0)

    ring.reset_liveness(now_monotonic=200.0)
    assert ring.segment_age_seconds(now_monotonic=200.5) == pytest.approx(0.5)
    assert not ring.is_stalled(3.0, now_monotonic=202.9)


@pytest.mark.parametrize(
    'segment_id,expected',
    [(0, 'seg_000000.ts'), (7, 'seg_000007.ts'), (123456, 'seg_123456.ts')],
)
def test_segment_filename_is_zero_padded(segment_id, expected):
    """`%06d`와 맞아야 한다. splitmuxsink의 location 패턴이 이 형식이다."""
    assert segment_filename(segment_id) == expected


# ----------------------------------------------------------------------
# 마이크 장애 판정 (S15P11A301-131)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    'source',
    ['pulsesrc0', 'audio_queue', 'voaacenc0', 'aacparse0', 'audioconvert1',
     'audioresample0', 'alsasrc0', 'GstPulseSrc:pulsesrc0'],
)
def test_audio_element_names_are_recognized(source):
    """오디오 브랜치의 오류를 오디오 오류로 판정한다.

    이 판정이 틀리면 노드가 마이크 장애를 비디오 장애로 보고, 오디오를 그대로
    둔 채 재구성해 같은 실패를 무한히 반복한다. 그동안 녹화가 멈춘다.

    입력은 노드가 넘기는 `message.src.get_name()`이다. 실제 마이크 실패에서
    확인한 소스가 `pulsesrc0`다.
    """
    from sentinel_streaming.ring_buffer import is_audio_element

    assert is_audio_element(source)


@pytest.mark.parametrize(
    'source',
    ['src', 'enc', 'record_queue', 'stream_queue', 'rtsp', 'ring',
     'nvv4l2decoder0', 'jpegparse0', 'h264parse0', 't', 'eos'],
)
def test_video_element_names_are_not_mistaken_for_audio(source):
    """비디오·출력 경로의 오류를 오디오 오류로 보면 안 된다.

    오디오를 끄면 소리가 사라지는데, 원인이 MediaMTX 재시작이면 소리를 끄고도
    문제가 그대로 남는다. 게다가 한 번 끄면 다시 켜지지 않는다.
    """
    from sentinel_streaming.ring_buffer import is_audio_element

    assert not is_audio_element(source)


def test_audio_hints_cover_every_element_in_the_branch(tmp_path):
    """브랜치 문자열의 요소가 모두 판정 대상이어야 한다.

    브랜치를 고치면서 요소를 추가하고 힌트를 빠뜨리면, 그 요소가 낸 오류는
    비디오 오류로 오분류된다. 두 곳이 어긋나는 순간을 여기서 잡는다.
    """
    from sentinel_streaming.ring_buffer import is_audio_element

    branch = writer(tmp_path, audio_enabled=True).audio_branch_description()
    for stage in branch.split('!'):
        element = _element_name(stage)
        if element is None:
            continue
        assert is_audio_element(element), f'{element}이 힌트 목록에 없다'


def _element_name(stage: str) -> str | None:
    """파이프라인 조각에서 GStreamer가 붙일 이름을 뽑는다. caps와 pad는 None.

    `name=`이 있으면 그것이 이름이고, 없으면 GStreamer가 팩토리 이름에 번호를
    붙인다(`pulsesrc` → `pulsesrc0`). 노드가 받는 `get_name()`이 이 값이다.

    팩토리 이름만 보면 안 된다. `queue name=audio_queue`의 팩토리는 `queue`이고
    그것은 비디오 쪽 `record_queue`와 구별되지 않는다. 실제로 이 시험이 그렇게
    틀린 추출을 잡아냈다.
    """
    tokens = stage.strip().split()
    if not tokens:
        return None
    if tokens[0].startswith(('audio/x-raw', 'video/x-', 'ring.')):
        return None
    for token in tokens[1:]:
        if token.startswith('name='):
            return token[len('name='):]
    return tokens[0]

"""파이프라인 불변식 테스트 (S15P11A301-186).

실기기 없이 도는 테스트다. 여기서 막으려는 회귀는 둘 다 **동작은 정상이고
지표는 좋아 보이는데 결과가 안 나오는** 종류라 사람 눈으로는 안 잡힌다.
"""

import sys
from pathlib import Path

import pytest

# CI는 저장소 루트에서 pytest를 돌리므로 패키지가 sys.path에 없다.
# 이 저장소의 다른 테스트와 같은 방식으로 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_streaming.pipeline_spec import (  # noqa: E402
    downscale_precedes_decode,
    keyframe_interval_seconds,
)

DECODER = 'nvv4l2decoder'


def _description(rate_before_decode: bool) -> str:
    """186에서 실제로 써 본 두 배치를 재현한다."""
    rate = '! videorate drop-only=true ! image/jpeg,framerate=15/1 '
    decode = f'! {DECODER} ! video/x-raw,format=I420 '
    head = 'appsrc name=src caps=image/jpeg,framerate=30/1 ! jpegparse '
    tail = '! x264enc name=enc ! h264parse ! tee name=t'
    if rate_before_decode:
        return head + rate + decode + tail
    return head + decode + rate.replace('image/jpeg', 'video/x-raw') + tail


def test_레이트_축소가_디코딩보다_앞서면_통과한다():
    assert downscale_precedes_decode(_description(True), DECODER)


def test_디코딩_뒤에_두면_기각한다():
    """186의 1차 시도. CPU는 내려갔지만 추론은 그대로였다."""
    assert not downscale_precedes_decode(_description(False), DECODER)


def test_videorate가_없으면_기각한다():
    plain = f'appsrc ! jpegparse ! {DECODER} ! x264enc'
    assert not downscale_precedes_decode(plain, DECODER)


def test_디코더가_폴백으로_바뀌어도_판정한다():
    """decoder는 설정값이고 jpegdec로 폴백한다. 이름을 박아두면 안 된다."""
    fallback = 'appsrc ! jpegparse ! videorate ! image/jpeg ! jpegdec ! x264enc'
    assert downscale_precedes_decode(fallback, 'jpegdec')
    assert not downscale_precedes_decode(
        'appsrc ! jpegparse ! jpegdec ! videorate ! x264enc', 'jpegdec'
    )


def test_키프레임_간격이_1초면_링_조각이_유지된다():
    assert keyframe_interval_seconds(15, 15) == pytest.approx(1.0)
    assert keyframe_interval_seconds(30, 30) == pytest.approx(1.0)


def test_encode_framerate만_내리면_간격이_2초가_된다():
    """186에서 encoder_key_int_max를 함께 안 내렸다면 생겼을 회귀."""
    assert keyframe_interval_seconds(30, 15) == pytest.approx(2.0)


def test_encode_fps가_0이면_예외다():
    with pytest.raises(ValueError):
        keyframe_interval_seconds(15, 0)

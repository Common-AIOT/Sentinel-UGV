"""파이프라인 문자열이 지켜야 하는 불변식(S15P11A301-186).

rclpy를 import하지 않는다. 파이프라인 구성의 제약을 CI에서 검사할 수 있게
순수 함수로 둔다(`command_relay.py`, `geometry.py`와 같은 이유다).

여기 있는 규칙은 둘 다 **실측으로 대가를 치르고 알아낸 것**이라 주석이 길다.
문자열 한 줄을 옮기면 조용히 깨지고, 깨져도 파이프라인은 정상 동작해 보인다.
"""

from __future__ import annotations

# 프레임을 버리는 요소. 이 이름이 바뀌면 검사도 같이 고쳐야 한다.
_RATE_ELEMENT = 'videorate'


def downscale_precedes_decode(description: str, decoder: str) -> bool:
    """레이트 축소가 디코딩보다 앞서는지.

    S15P11A301-186에서 이 순서를 틀렸다. `videorate`를 디코더 **뒤**(인코더
    직전)에 두었더니 x264enc CPU는 85.7%에서 58.9%로 내려갔는데 AI 추론은
    13.33 FPS 그대로였다. 버릴 프레임을 디코딩한 뒤에 버렸기 때문이다.
    디코더 앞으로 옮기고 나서야 13.82 FPS가 됐다.

    이 실수가 위험한 이유는 **성공처럼 보인다**는 점이다. CPU 사용률이 실제로
    내려가므로 지표만 보면 고친 것 같다. 그래서 순서를 코드로 못박는다.

    Orin Nano는 CPU와 GPU가 LPDDR5 대역폭을 공유한다. 720p MJPEG 디코딩이 그
    대역폭의 큰 소비자이고, 추론도 같은 대역폭을 쓴다. 압축 상태로 버려야
    디코딩과 인코딩이 함께 줄어든다.
    """
    if _RATE_ELEMENT not in description:
        return False
    if decoder not in description:
        return False
    return description.index(_RATE_ELEMENT) < description.index(decoder)


def keyframe_interval_seconds(key_int_max: int, encode_fps: int) -> float:
    """IDR이 몇 초마다 나오는지.

    링 버퍼는 이 값이 1.0이라는 전제에 기댄다. `ring_buffer.py`가
    `send-keyframe-requests`를 false로 두면서 "인코더의 key-int-max가 이미
    1초마다 IDR을 만든다"를 근거로 삼기 때문이다.

    1.0이 아니면 splitmuxsink가 `max-size-time`(segment_seconds)에 도달해도
    쪼갤 IDR이 없어 조각이 길어진다. 사전 영상 3초의 granularity가 무너지고
    `ring_stall_timeout_seconds` 여유도 줄어든다.
    """
    if encode_fps <= 0:
        raise ValueError(f'encode_fps는 양수여야 한다: {encode_fps}')
    return key_int_max / encode_fps

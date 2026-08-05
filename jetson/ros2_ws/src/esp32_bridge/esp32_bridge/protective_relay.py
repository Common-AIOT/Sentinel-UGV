"""초음파 보호정지 → `STOP_COMMAND` 중계 판정 (S15P11A301-237, 명세 03-276).

명세 03-276 은 "Jetson 이 `protective_stop` 을 받아 **즉시** 모터 ESP32 에
`STOP_COMMAND` 를 중계" 로 정한다. 그 중계가 없었다 — `safety_gate` 는
`/cmd_vel` 을 0 으로 만들지만 `STOP_COMMAND`(0x11)가 추가로 하는 것(브레이크·
driver disable)은 발동하지 않았다. 이 모듈이 그 판정이다.

## 왜 게이트가 아니라 브리지에 두는가

`safety_gate` 도 같은 토픽을 구독한다. 두 경로는 **중복이 아니라 서로 다른 것을
막는다.**

- 게이트: 속도 흐름을 0 으로 만든다 → 상위가 계속 명령해도 주행이 재개되지 않는다
- 브리지: `STOP_COMMAND` 로 브레이크·driver disable 을 건다 → 관성 주행을 줄인다

브리지에 직접 두는 이유는 둘이다. **지연** — 게이트가 서비스로 부르면 홉이 하나
늘고, 300ms watchdog 목표에서 홉마다의 지연이 아깝다. **독립성** — 게이트가 죽어도
보호정지는 전달돼야 한다. 게이트를 거치게 만들면 안전 경로가 게이트 생존에 걸린다.

## 왜 엣지 기반인가

`PROXIMITY_STATE` 는 10~20Hz 로 오고(명세 03 메시지 표) 그때마다
`protective_stop` 이 실린다. 매번 중계하면 같은 시리얼 링크에서 50Hz
`DRIVE_COMMAND` 와 다투고, 정작 급할 때 프레임이 밀린다. 그래서 상승 엣지에만
보낸다.

**상승에서는 3회 연속 보낸다.** 명세 03 메시지 표가 `STOP_COMMAND`(0x11)의 주기를
"즉시, 3회 반복 전송" 으로 정한다 — 프레임 하나가 유실돼도 정지가 전달되게 하는
장치이며, 정지는 ACK 를 기다리지 않고 로컬 실행이므로(03 "안전 정지는 통신 ACK를
기다리지 않고 로컬에서 실행") 재전송이 유일한 보장이다.

눌린 동안에는 `reassert_period_s` 마다 1회 다시 보낸다. 3회 버스트가 유실을
덮으므로 이것은 다른 것을 위한 것이다 — **눌린 채로 ESP32 가 재부팅하면** 보드가
정지 상태를 잊는데, 상승 엣지는 이미 지나가 다시 오지 않는다. 재확인이 없으면
브레이크·driver disable 이 복구되지 않는다.

센서 브리지가 죽어서 메시지 자체가 끊기면 이 모듈은 아무것도 하지 않는다. 그
경우는 게이트가 `PROXIMITY_STALE` 로 `/cmd_vel` 을 0 으로 만들어 막는다 —
막는 층이 다르다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 눌린 동안 다시 보내는 간격. 200ms 는 ESP32 통신 watchdog(300ms)보다 짧아
# 프레임 하나를 잃어도 watchdog 이 걸리기 전에 두 번째가 도착한다.
DEFAULT_REASSERT_PERIOD_S = 0.2

# 주기 비교의 허용오차. 이것이 없으면 `0.6 - 0.4 = 0.19999999999999996` 같은
# 부동소수 결과가 `>= 0.2` 를 거짓으로 만들어 재확인이 한 주기 밀린다. 실제
# 영향은 다음 메시지까지의 수십 ms 로 작지만, 경계에서 동작이 입력 표현에
# 따라 달라지는 것은 시험으로 고정할 수 없다. 1µs 는 이 판정의 시간 규모
# (100ms 단위)에서 무의미한 크기다.
_PERIOD_EPSILON_S = 1e-6

REASON_RISING = 'RISING'
REASON_REASSERT = 'REASSERT'
REASON_RELEASED = 'RELEASED'
REASON_HELD = 'HELD'
REASON_CLEAR = 'CLEAR'


# 상승 엣지의 반복 횟수. 명세 03 메시지 표가 정한 값이다("즉시, 3회 반복 전송").
RISING_REPEAT = 3


@dataclass(frozen=True)
class RelayDecision:
    """`repeat` 번 `STOP_COMMAND` 를 보낸다. 0 이면 보내지 않는다.

    `send` 는 `repeat > 0` 의 별칭이다 — 호출부가 `if decision.send:` 로 읽는
    쪽이 자연스러운 곳이 있다.
    """

    repeat: int
    reason: str

    @property
    def send(self) -> bool:
        return self.repeat > 0


class ProtectiveStopRelay:
    """보호정지 신호의 엣지를 보고 중계 시점을 정한다.

    시계를 스스로 읽지 않는다 — 호출부가 `now_s` 를 준다. 그래야 rclpy 없이
    시험할 수 있고, 시험이 sleep 으로 시간을 흘리지 않아도 된다.
    """

    def __init__(self, reassert_period_s: float = DEFAULT_REASSERT_PERIOD_S) -> None:
        self.reassert_period_s = reassert_period_s
        # None 은 "아직 한 번도 못 받았다" 다. False 로 초기화하면 첫 메시지가
        # True 일 때 그것이 상승 엣지인지 원래 눌려 있었는지 구별할 수 없다.
        self._active: bool | None = None
        self._last_sent_s: float | None = None

    @property
    def active(self) -> bool | None:
        return self._active

    def observe(self, active: bool, now_s: float) -> RelayDecision:
        previous = self._active
        self._active = active

        if not active:
            # 해제는 아무것도 보내지 않는다. 상위가 다시 명령을 내면 그것이
            # 그대로 나가면 된다 — 여기서 "재개" 를 보내면 정지 판단 주체가
            # 둘이 된다.
            #
            # `_last_sent_s` 를 여기서 지우지 않는다. 지우는 코드가 있었으나
            # 효과가 없었다 — 해제 후 재확인 분기로 돌아오려면 반드시 아래
            # 상승 분기를 거치고 그쪽이 시계를 새로 쓴다. 뮤테이션 시험에서
            # 그 줄을 지워도 아무 시험이 깨지지 않아 죽은 코드임이 드러났다.
            return RelayDecision(0, REASON_RELEASED if previous else REASON_CLEAR)

        if previous is not True:
            # 미수신 → True 도 상승으로 본다. 기동 직후 이미 눌려 있는 상태를
            # "변화 없음" 으로 읽으면 그 정지가 영원히 전달되지 않는다.
            self._last_sent_s = now_s
            return RelayDecision(RISING_REPEAT, REASON_RISING)

        if (
            self._last_sent_s is None
            or now_s - self._last_sent_s >= self.reassert_period_s - _PERIOD_EPSILON_S
        ):
            self._last_sent_s = now_s
            # 재확인은 1회다. 유실 대비는 상승의 3회가 이미 했고, 이것은 ESP32
            # 재부팅 복구용이라 매번 3회를 쓸 이유가 없다.
            return RelayDecision(1, REASON_REASSERT)

        return RelayDecision(0, REASON_HELD)

"""보호정지 중계 판정 시험 (S15P11A301-237).

rclpy 없이 돈다 — `ProtectiveStopRelay` 가 시계를 스스로 읽지 않고 `now_s` 를
받기 때문이다. 시험이 sleep 으로 시간을 흘리지 않는다.
"""

from esp32_bridge.protective_relay import (DEFAULT_REASSERT_PERIOD_S,
                                           RISING_REPEAT,
                                           ProtectiveStopRelay)


def test_상승은_명세대로_3회_반복이다():
    """명세 03 메시지 표: STOP_COMMAND 는 "즉시, 3회 반복 전송". 정지는 ACK 를
    기다리지 않고 로컬 실행이라 재전송이 유실에 대한 유일한 보장이다."""
    assert RISING_REPEAT == 3

    relay = ProtectiveStopRelay()

    assert relay.observe(True, 0.0).repeat == 3


def test_재확인은_1회다():
    """유실 대비는 상승의 3회가 했다. 재확인은 ESP32 재부팅 복구용이다."""
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)

    assert relay.observe(True, 0.2).repeat == 1


def test_안_보낼_때는_repeat이_0이다():
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)

    assert relay.observe(True, 0.05).repeat == 0
    assert relay.observe(False, 0.3).repeat == 0


def test_첫_수신이_눌림이면_상승으로_보낸다():
    """기동 직후 이미 눌려 있는 상태를 놓치면 그 정지가 영원히 전달되지 않는다."""
    relay = ProtectiveStopRelay()

    decision = relay.observe(True, 0.0)

    assert decision.send is True
    assert decision.reason == 'RISING'


def test_첫_수신이_해제면_아무것도_안_보낸다():
    relay = ProtectiveStopRelay()

    decision = relay.observe(False, 0.0)

    assert decision.send is False
    assert decision.reason == 'CLEAR'


def test_해제에서_눌림으로_바뀌면_보낸다():
    relay = ProtectiveStopRelay()
    relay.observe(False, 0.0)

    decision = relay.observe(True, 0.05)

    assert decision.send is True
    assert decision.reason == 'RISING'


def test_눌린_동안_재확인_주기_전에는_안_보낸다():
    """수십 Hz 로 오는 신호를 매번 중계하면 DRIVE_COMMAND 와 시리얼을 다툰다."""
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)

    for t in (0.02, 0.05, 0.10, 0.19):
        decision = relay.observe(True, t)
        assert decision.send is False, f"t={t} 에서 보냈다"
        assert decision.reason == 'HELD'


def test_눌린_동안_재확인_주기가_지나면_다시_보낸다():
    """프레임 하나가 유실돼도 정지가 전달되게 하는 것이 재확인의 목적이다."""
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)
    relay.observe(True, 0.1)

    decision = relay.observe(True, 0.2)

    assert decision.send is True
    assert decision.reason == 'REASSERT'


def test_재확인은_반복된다():
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)

    sent = [t for t in (0.2, 0.4, 0.6) if relay.observe(True, t).send]

    assert sent == [0.2, 0.4, 0.6]


def test_해제는_아무것도_보내지_않는다():
    """여기서 재개를 보내면 정지 판단 주체가 둘이 된다."""
    relay = ProtectiveStopRelay()
    relay.observe(True, 0.0)

    decision = relay.observe(False, 0.3)

    assert decision.send is False
    assert decision.reason == 'RELEASED'


def test_해제_후_재진입은_다시_상승이다():
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)
    relay.observe(False, 0.1)

    decision = relay.observe(True, 0.15)

    assert decision.send is True
    # 재확인 주기가 안 지났어도 상승이므로 즉시 보낸다.
    assert decision.reason == 'RISING'


def test_재진입이_재확인_시계를_새로_시작한다():
    """해제 후 재진입에서 '직전에 보냈다'는 이유로 지연되면 안 된다.

    시계를 새로 쓰는 것은 상승 분기다 — 해제 분기가 아니다. 해제에서 지우는
    코드는 효과가 없어 지웠다(모듈 주석 참조)."""
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 1.0)
    relay.observe(False, 1.05)
    relay.observe(True, 1.06)

    # 재진입 직후의 유지 구간은 새 시계를 기준으로 센다.
    assert relay.observe(True, 1.20).send is False
    assert relay.observe(True, 1.26).send is True


def test_누적된_시각이_경계에_떨어져도_재확인이_밀리지_않는다():
    """`0.6 - 0.4 == 0.19999999999999996` 같은 부동소수 결과가 판정을 바꾸면
    안 된다. 허용오차 없이는 이 세 번째 재확인이 한 주기 밀렸다."""
    relay = ProtectiveStopRelay(reassert_period_s=0.2)
    relay.observe(True, 0.0)

    assert relay.observe(True, 0.2).send is True
    assert relay.observe(True, 0.4).send is True
    assert relay.observe(True, 0.6).send is True


def test_기본_재확인_주기는_watchdog보다_짧다():
    """300ms watchdog 이 걸리기 전에 두 번째가 도착해야 유실이 복구된다."""
    assert DEFAULT_REASSERT_PERIOD_S < 0.3


def test_active_속성이_미수신을_구별한다():
    relay = ProtectiveStopRelay()
    assert relay.active is None

    relay.observe(False, 0.0)
    assert relay.active is False

    relay.observe(True, 0.1)
    assert relay.active is True

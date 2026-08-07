"""링크 판정 시험 (S15P11A301-323).

고정하는 것은 **두 실패를 가르는 것**이다. 「보드가 말이 없다」와 「말은 하는데
우리가 버린다」가 같은 문장으로 나오면 이 모듈은 존재 이유가 없다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.link_health import (  # noqa: E402
    ERROR,
    OK,
    WARN,
    environment_verdict,
    motor_link_verdict,
)


def motor(**over):
    base = dict(
        handshake_ok=True,
        rx_frame_count=100,
        parse_error_count=0,
        parse_errors_by_type={},
        hello_sent_count=1,
        since_last_rx_s=0.05,
    )
    base.update(over)
    return motor_link_verdict(**base)


class TestMotorLink:
    def test_정상은_ok(self):
        assert motor().level == OK

    def test_프레임_0건은_무응답이다(self):
        # 2026-08-06 실기동에서 겪은 그 상태다. 종전에는 진단 항목 자체가 없었다.
        v = motor(handshake_ok=False, rx_frame_count=0, hello_sent_count=40, since_last_rx_s=None)
        assert v.level == ERROR
        assert '무응답' in v.message
        assert v.values['rx_frame_count'] == '0'
        assert v.values['hello_sent_count'] == '40'

    def test_프레임은_오는데_해석_실패는_다른_문장이다(self):
        # 보레이트·프로토콜 불일치. 무응답과 헷갈리면 원인 후보가 안 좁혀진다.
        v = motor(
            handshake_ok=False,
            rx_frame_count=57,
            parse_error_count=57,
            parse_errors_by_type={'CrcError': 57},
        )
        assert v.level == ERROR
        assert '해석하지 못한다' in v.message
        assert '무응답' not in v.message
        assert v.values['parse_error.CrcError'] == '57'

    def test_프레임은_오는데_ack이_없으면_핸드셰이크_미완(self):
        v = motor(handshake_ok=False, parse_error_count=0)
        assert v.level == ERROR
        assert '핸드셰이크' in v.message

    def test_수신이_끊기면_경고(self):
        v = motor(since_last_rx_s=5.0)
        assert v.level == WARN
        assert '5.0s' in v.message

    def test_간헐_파싱_실패는_경고로만_보인다(self):
        v = motor(parse_error_count=3, parse_errors_by_type={'CobsError': 3})
        assert v.level == WARN
        assert v.values['parse_error_count'] == '3'


class TestEnvironment:
    def test_정상은_ok(self):
        v = environment_verdict(
            received_count=30, published_count=30, dropped_by_flag={}, last_status_flags=0
        )
        assert v.level == OK

    def test_미수신과_전부_버림은_다른_문장이다(self):
        missing = environment_verdict(
            received_count=0, published_count=0, dropped_by_flag={}, last_status_flags=None
        )
        dropped = environment_verdict(
            received_count=30, published_count=0, dropped_by_flag={2: 30}, last_status_flags=2
        )
        assert missing.level == WARN and '미수신' in missing.message
        assert dropped.level == ERROR and '전부 버림' in dropped.message
        # 이 구분이 없어서 정상 동작이 「펌웨어가 다르다」로 오진됐다.
        assert missing.message != dropped.message

    def test_간헐_실패는_비율과_fault_비트_설명을_함께_낸다(self):
        # 2026-08-07 실측: 15초에 4건만 유효. 보드 fault 는 연속 3회에만 뜬다.
        v = environment_verdict(
            received_count=15, published_count=4, dropped_by_flag={1: 8, 2: 3},
            last_status_flags=1,
        )
        assert v.level == WARN
        assert '11건 버림' in v.message
        assert '73%' in v.message
        assert '연속 3회' in v.message
        assert v.values['dropped.0x01'] == '8'
        assert v.values['dropped.0x02'] == '3'

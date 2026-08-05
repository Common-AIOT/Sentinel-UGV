"""명령 중재 시험 (S15P11A301-237).

가장 위험한 실패는 **두 소스가 동시에 통과하는 것**이다. 자율과 앱이 같은 모터를
서로 다른 방향으로 밀면 지령과 실제 움직임의 관계가 깨져 원인 추적이 불가능해진다.
여기서는 "지정된 쪽 하나만" 을 값으로 못박는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_safety.mux import (  # noqa: E402
    MODE_AUTO,
    MODE_MANUAL,
    SOURCE_AUTO,
    SOURCE_MANUAL,
    SOURCE_NONE,
    select,
)

AUTO = (0.25, 0.1)
MANUAL = (-0.15, -0.4)


# ── 지정된 쪽만 통과 ─────────────────────────────────────────────────────────

def test_자율에서는_nav_만_통과한다():
    decision = select(MODE_AUTO, auto=AUTO, manual=MANUAL)
    assert decision.source == SOURCE_AUTO
    assert (decision.linear_mps, decision.angular_radps) == AUTO


def test_수동에서는_manual_만_통과한다():
    decision = select(MODE_MANUAL, auto=AUTO, manual=MANUAL)
    assert decision.source == SOURCE_MANUAL
    assert (decision.linear_mps, decision.angular_radps) == MANUAL


def test_수동에서_nav_명령은_차단된다():
    # 완료 기준: "수동 모드에서 Nav2 명령이 차단되고, 자율 모드에서 수동 명령이
    # 차단된다". 수동인데 manual 발행자가 없는 것이 현재 정상 상태다 —
    # 모바일 앱이 젯슨을 거치지 않는다(S15P11A301-259).
    decision = select(MODE_MANUAL, auto=AUTO, manual=None)
    assert decision.source == SOURCE_NONE
    assert decision.linear_mps == 0.0
    assert 'MANUAL' in decision.reason


def test_자율에서_manual_명령은_차단된다():
    decision = select(MODE_AUTO, auto=None, manual=MANUAL)
    assert decision.source == SOURCE_NONE
    assert decision.linear_mps == 0.0


# ── 모드를 모르면 아무것도 통과시키지 않는다 ─────────────────────────────────

def test_모드가_없으면_막는다():
    # 기동 직후 /mission/status 를 받기 전. 기본값을 자율로 두면 그 사이에
    # Nav2 잔여 명령이 통과한다.
    decision = select(None, auto=AUTO, manual=MANUAL)
    assert decision.source == SOURCE_NONE
    assert (decision.linear_mps, decision.angular_radps) == (0.0, 0.0)


def test_모르는_모드값도_막는다():
    for mode in ['', 'auto', 'Auto', 'AUTONOMOUS', 'STOP']:
        decision = select(mode, auto=AUTO, manual=MANUAL)
        assert decision.source == SOURCE_NONE, mode


def test_모드_문자열은_대문자_정확히_일치해야_한다():
    # state.schema.json 의 값이 계약이다. 소문자를 받아들이면 스키마가 바뀐 것을
    # 눈치채지 못하고 지나간다.
    assert select('AUTO', auto=AUTO, manual=None).source == SOURCE_AUTO
    assert select('auto', auto=AUTO, manual=None).source == SOURCE_NONE


# ── 차단 이유 ────────────────────────────────────────────────────────────────

def test_차단에는_항상_이유가_붙는다():
    for mode in [None, 'NONSENSE', MODE_AUTO]:
        decision = select(mode, auto=None, manual=None)
        assert decision.source == SOURCE_NONE
        assert decision.reason, mode


def test_통과할_때는_이유가_비어_있다():
    assert select(MODE_AUTO, auto=AUTO, manual=None).reason == ''


def test_0_지령은_유효한_명령이다():
    # (0.0, 0.0) 이 None 과 같게 취급되면 "정지 지령" 이 "명령 없음" 이 되고,
    # 그러면 상류가 의도적으로 낸 정지가 차단 로그로 남는다.
    decision = select(MODE_AUTO, auto=(0.0, 0.0), manual=None)
    assert decision.source == SOURCE_AUTO
    assert decision.reason == ''

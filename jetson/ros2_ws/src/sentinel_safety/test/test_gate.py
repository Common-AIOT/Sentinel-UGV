"""최종 게이트 판정 시험 (S15P11A301-237).

이 층이 틀리면 **틀린 방향이 둘**이고 성질이 다르다. 통과시켜야 할 때 막으면
"로봇이 안 움직인다" 로 즉시 드러나지만, 막아야 할 때 통과시키면 아무 증상이
없고 사고로만 드러난다. 그래서 "막는다" 쪽을 값으로 못박는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# CI(alpine venv)는 패키지를 설치하지 않고 리포 루트에서 pytest 를 돌린다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_safety.gate import (  # noqa: E402
    GateInputs,
    GateTimeouts,
    evaluate,
)

T = 100.0  # 시험용 현재 시각


def healthy(**overrides) -> GateInputs:
    """모든 조건이 통과인 입력. 각 시험은 여기서 하나만 망가뜨린다."""
    base = dict(
        now_s=T,
        linear_mps=0.25,
        angular_radps=0.1,
        command_stamp_s=T - 0.01,
        mission_state='EXPLORING',
        mission_stamp_s=T - 1.0,
        movement_allowed=True,
        protective_stop=False,
        protective_stamp_s=T - 0.1,
        scan_stamp_s=T - 0.05,
    )
    base.update(overrides)
    return GateInputs(**base)


def codes(decision) -> set[str]:
    return {reason.split(':')[0] for reason in decision.reasons}


# ── 통과 ─────────────────────────────────────────────────────────────────────

def test_전부_정상이면_지령을_그대로_낸다():
    decision = evaluate(healthy())
    assert not decision.blocked
    assert decision.linear_mps == 0.25
    assert decision.angular_radps == 0.1


def test_주행_상태들에서_통과한다():
    for state in ['EXPLORING', 'PERSON_APPROACHING', 'RETURNING', 'MANUAL']:
        decision = evaluate(healthy(mission_state=state))
        assert not decision.blocked, state


# ── 침묵은 이상 없음이 아니다 ────────────────────────────────────────────────

def test_한_번도_안_온_신호는_전부_낡음이다():
    # 기동 직후 상태. None 을 통과로 다루면 아무 보호 없이 첫 명령이 나간다.
    decision = evaluate(GateInputs(now_s=T, linear_mps=0.25, angular_radps=0.0))
    assert decision.blocked
    assert codes(decision) == {
        'COMMAND_STALE', 'MISSION_STALE', 'PROXIMITY_STALE', 'SCAN_STALE'
    }


def test_초음파_침묵을_장애물_없음으로_읽지_않는다():
    # 센서 보드가 빠지면 protective_stop 이 조용해진다 — "false" 와 구별되지
    # 않으므로 침묵 자체를 정지 사유로 쓴다(S15P11A301-258 에서 실제로 겪었다).
    decision = evaluate(healthy(protective_stamp_s=T - 5.0))
    assert 'PROXIMITY_STALE' in codes(decision)
    assert decision.linear_mps == 0.0


def test_scan_침묵도_정지_사유다():
    # collision_monitor 는 낡은 소스를 건너뛰므로 라이다가 죽으면 그 층이
    # 통과시킨다. 마지막 관문이 막아야 한다.
    decision = evaluate(healthy(scan_stamp_s=T - 1.0))
    assert 'SCAN_STALE' in codes(decision)
    assert decision.linear_mps == 0.0


def test_명령_TTL_경계():
    limits = GateTimeouts(command_ttl_s=0.3)
    assert not evaluate(healthy(command_stamp_s=T - 0.29), limits).blocked
    assert 'COMMAND_STALE' in codes(evaluate(healthy(command_stamp_s=T - 0.31), limits))


def test_임무_상태가_낡으면_상태값을_믿지_않는다():
    # mission_manager 가 죽었는데 마지막 EXPLORING 을 계속 신뢰하면, 관제가
    # 정지시킬 방법이 사라진다.
    decision = evaluate(healthy(mission_stamp_s=T - 30.0))
    assert 'MISSION_STALE' in codes(decision)
    # 낡았으면 그 안의 movementAllowed 도 판정에 쓰지 않는다 — 이유를 두 번
    # 내지 않는다(원인이 하나인데 줄이 둘이면 둘을 따로 고치려 한다).
    assert 'MOVEMENT_NOT_ALLOWED' not in codes(decision)


# ── 임무 상태 ────────────────────────────────────────────────────────────────

def test_정지_상태들에서_막는다():
    for state in ['SAFE_IDLE', 'PAUSED', 'ESTOP', 'ERROR', 'COMPLETED']:
        decision = evaluate(healthy(mission_state=state))
        assert 'MISSION_STATE' in codes(decision), state
        assert decision.linear_mps == 0.0


def test_PAUSED_에서는_Nav2_지령이_0이_된다():
    # 완료 기준: "Nav2 가 0.25 m/s 를 지령해도 PAUSED 에서 모터 명령이 0"
    decision = evaluate(healthy(mission_state='PAUSED', linear_mps=0.25))
    assert decision.linear_mps == 0.0
    assert decision.angular_radps == 0.0


def test_movement_allowed_가_거짓이면_막는다():
    assert 'MOVEMENT_NOT_ALLOWED' in codes(evaluate(healthy(movement_allowed=False)))


def test_movement_allowed_가_없으면_막는다():
    # None 은 "판단 근거가 없다" 다. 참으로 간주하면 스키마가 바뀌어 필드가
    # 사라졌을 때 보호가 조용히 없어진다.
    assert 'MOVEMENT_NOT_ALLOWED' in codes(evaluate(healthy(movement_allowed=None)))


def test_모르는_상태값은_movement_allowed_로만_판정한다():
    # 26.2 에 없는 상태가 오면 이름으로는 막지 않지만 movementAllowed 는 본다.
    assert not evaluate(healthy(mission_state='SOMETHING_NEW')).blocked
    assert evaluate(
        healthy(mission_state='SOMETHING_NEW', movement_allowed=False)
    ).blocked


# ── 초음파 ───────────────────────────────────────────────────────────────────

def test_보호정지는_Nav2_목표와_무관하게_막는다():
    # 24장: "사람 접근 중 안전거리보다 가까워지면 Nav2 목표와 무관하게 정지"
    decision = evaluate(
        healthy(mission_state='PERSON_APPROACHING', protective_stop=True)
    )
    assert 'PROTECTIVE_STOP' in codes(decision)
    assert decision.linear_mps == 0.0


def test_후진도_막는다():
    # 보호정지에서 전진만 막고 후진을 통과시키는 설계도 있지만, 초음파는
    # 전방 1개뿐이라 후방을 못 본다. 뒤가 안전하다는 근거가 없으므로 둘 다 막는다.
    decision = evaluate(healthy(protective_stop=True, linear_mps=-0.2))
    assert decision.linear_mps == 0.0


# ── 이유를 모두 낸다 ─────────────────────────────────────────────────────────

def test_이유가_여러_개면_전부_낸다():
    # 첫 이유에서 멈추면, 그것을 고친 뒤에도 안 움직이는 원인을 처음부터
    # 다시 찾는다.
    decision = evaluate(
        healthy(
            mission_state='PAUSED',
            movement_allowed=False,
            protective_stop=True,
            scan_stamp_s=T - 9.0,
        )
    )
    assert codes(decision) == {
        'MISSION_STATE', 'MOVEMENT_NOT_ALLOWED', 'PROTECTIVE_STOP', 'SCAN_STALE'
    }
    assert len(decision.reasons) == 4


def test_이유에_사람이_읽을_설명이_붙는다():
    # 코드만 있으면 로그를 봐도 무엇을 확인해야 하는지 모른다.
    decision = evaluate(healthy(command_stamp_s=None))
    reason = next(r for r in decision.reasons if r.startswith('COMMAND_STALE'))
    assert ':' in reason and len(reason.split(':', 1)[1].strip()) > 10


def test_통과할_때는_이유가_비어_있다():
    assert evaluate(healthy()).reasons == ()


# ----------------------------------------------------------------------
# MANUAL 은 차단 집합에 없다 (S15P11A301-298)
# ----------------------------------------------------------------------


def test_manual은_차단_상태_집합에_없다():
    """이 집합의 뜻은 "사람이 풀어야 한다" 이고 MANUAL 은 다르다.

    넣으면 mux 가 남긴 `/cmd_vel_manual` 경로를 조용히 영구 봉쇄하고, 정지 사유가
    한 덩어리로 뭉개져 어느 층이 막았는지 알 수 없게 된다.
    """
    from sentinel_safety.gate import BLOCKING_MISSION_STATES

    assert 'MANUAL' not in BLOCKING_MISSION_STATES


def test_manual에서는_movement_not_allowed만_뜬다():
    """MANUAL 에서도 0 이 나가는 것은 같지만 사유가 하나다.

    PAUSED 는 둘(MISSION_STATE_BLOCKED + MOVEMENT_NOT_ALLOWED)이라 운영자가
    로그에서 두 경우를 구별할 수 있다.
    """
    manual = evaluate(healthy(mission_state='MANUAL', movement_allowed=False))
    paused = evaluate(healthy(mission_state='PAUSED', movement_allowed=False))

    assert manual.linear_mps == 0.0
    assert manual.angular_radps == 0.0
    assert len(manual.reasons) == 1
    assert manual.reasons[0].startswith('MOVEMENT_NOT_ALLOWED')
    assert len(paused.reasons) > len(manual.reasons)

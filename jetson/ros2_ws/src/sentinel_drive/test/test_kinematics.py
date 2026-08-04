"""역운동학 시험 (S15P11A301-234).

부호가 이 패키지의 전부다. 틀려도 로봇은 움직이고 오도메트리는 SLAM 이 보정해
버려서, 회전 명령에 반대로 도는 것으로만 드러난다 — 그때는 실차 위다. 여기서
값으로 못박는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# CI(alpine venv)는 패키지를 설치하지 않고 리포 루트에서 pytest 를 돌린다.
# 다른 패키지 시험(sentinel_bridge·mission)과 같은 관행이다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_drive.kinematics import (
    MODE_AUTO,
    MODE_MANUAL,
    drive_command,
    saturate,
    stop_command,
    wheel_speeds_mps,
)

W = 0.30  # 시험용 트랙폭. 실측 전 잠정값과 같은 수


# ── 역운동학 부호 — 정운동학(delta_yaw = (r-l)/W)과 왕복 일치 ────────────────

def test_전진은_양쪽_동일_양수():
    left, right = wheel_speeds_mps(0.25, 0.0, W)
    assert left == right == 0.25


def test_후진은_양쪽_동일_음수():
    left, right = wheel_speeds_mps(-0.25, 0.0, W)
    assert left == right == -0.25


def test_반시계_제자리_회전은_오른쪽이_전진():
    # REP-103: ω > 0 = 반시계. 정운동학 delta_yaw = (r-l)/W 이므로 r > l 이어야
    # 한다. 여기가 뒤집히면 회전 명령에 로봇이 반대로 돈다.
    left, right = wheel_speeds_mps(0.0, 1.0, W)
    assert right > 0 > left
    assert right == pytest.approx(0.15)   # ω·W/2 = 1.0 × 0.30 / 2
    assert left == pytest.approx(-0.15)


def test_시계_회전은_왼쪽이_전진():
    left, right = wheel_speeds_mps(0.0, -1.0, W)
    assert left > 0 > right


def test_정운동학과_왕복이_항등이다():
    # 역운동학 결과를 wheel_odometry 의 공식에 되넣으면 (v, ω) 가 나와야 한다.
    # 이 왕복이 두 패키지의 부호 계약이다.
    for v, w in [(0.25, 0.0), (0.0, 0.6), (0.2, -0.4), (-0.1, 0.3)]:
        left, right = wheel_speeds_mps(v, w, W)
        assert (left + right) / 2.0 == pytest.approx(v)
        assert (right - left) / W == pytest.approx(w)


def test_트랙폭이_클수록_같은_회전에_바퀴차가_크다():
    # W 를 절반으로 틀리면(줄자 실측 실수) 같은 ω 지령에 바퀴 차가 절반이 되고,
    # 실제 yaw rate 가 지령의 두 배가 된다 — TBD-CAL-002 가 급한 이유.
    _, right_narrow = wheel_speeds_mps(0.0, 1.0, 0.15)
    _, right_wide = wheel_speeds_mps(0.0, 1.0, 0.30)
    assert right_wide == pytest.approx(right_narrow * 2)


def test_트랙폭_0이하는_거부한다():
    with pytest.raises(ValueError):
        wheel_speeds_mps(0.1, 0.0, 0.0)


# ── 포화 — 곡률 보존 ─────────────────────────────────────────────────────────

def test_상한_이내면_그대로다():
    assert saturate(0.1, 0.2, 0.3) == (0.1, 0.2)


def test_상한_초과면_비율을_유지하며_줄인다():
    # 곡률 = (r-l)/(r+l) 비가 보존돼야 한다. 한쪽만 자르면 지령한 호가 아니라
    # 다른 방향으로 간다 — Nav2 컨트롤러가 보정하려다 진동한다.
    left, right = saturate(0.2, 0.6, 0.3)
    assert right == pytest.approx(0.3)          # 최대가 상한에 붙는다
    assert left == pytest.approx(0.1)           # 0.2 × (0.3/0.6)
    assert left / right == pytest.approx(0.2 / 0.6)


def test_음수_쪽이_큰_경우도_비율_유지():
    left, right = saturate(-0.6, 0.2, 0.3)
    assert left == pytest.approx(-0.3)
    assert right == pytest.approx(0.1)


def test_제자리_회전_포화도_대칭이다():
    left, right = saturate(-0.5, 0.5, 0.3)
    assert left == pytest.approx(-0.3)
    assert right == pytest.approx(0.3)


def test_상한_0이하는_거부한다():
    with pytest.raises(ValueError):
        saturate(0.1, 0.1, 0.0)


# ── JSON 계약 — esp32_motor_bridge._on_drive_command 가 받는 형태 ───────────

def test_계약_필수_필드와_mm_변환():
    cmd = drive_command(0.25, -0.15)
    assert cmd['mode'] == MODE_AUTO
    assert cmd['target_drive_left_mmps'] == 250
    assert cmd['target_drive_right_mmps'] == -150
    assert cmd['command_timeout_ms'] == 300
    # 브리지가 int() 로 파싱하므로 전부 정수여야 한다. float 이 섞이면
    # int("250.0") 이 ValueError 로 명령이 조용히 버려진다.
    assert all(isinstance(v, int) for v in cmd.values())


def test_반올림은_round_다():
    # int() 절단이면 0.6mm/s 가 0 이 된다 — 초저속에서 방향 자체가 죽는다.
    assert drive_command(0.0006, -0.0006)['target_drive_left_mmps'] == 1
    assert drive_command(0.0006, -0.0006)['target_drive_right_mmps'] == -1


def test_정지_명령():
    cmd = stop_command()
    assert cmd['target_drive_left_mmps'] == 0
    assert cmd['target_drive_right_mmps'] == 0
    assert cmd['mode'] == MODE_AUTO


def test_모드_치환():
    assert stop_command(mode=MODE_MANUAL)['mode'] == MODE_MANUAL


def test_모드_상수는_명세_값이다():
    # 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2 — ESP32 펌웨어가 u8 로 이 값을
    # 해석한다. 상수끼리 비교하면 상수가 바뀌어도 시험이 통과한다(동어반복).
    # 펌웨어 쪽 계약이므로 리터럴로 박는다.
    from sentinel_drive.kinematics import MODE_SAFE_IDLE
    assert MODE_SAFE_IDLE == 0
    assert MODE_MANUAL == 1
    assert MODE_AUTO == 2

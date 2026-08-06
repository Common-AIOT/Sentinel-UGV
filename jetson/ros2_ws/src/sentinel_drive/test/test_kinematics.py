"""전륜 조향 역운동학 시험 (S15P11A301-234·297).

부호와 거부 규칙이 이 패키지의 전부다. 틀려도 로봇은 움직이고 오도메트리는 SLAM 이
보정해 버려서, 회전 명령에 반대로 도는 것으로만 드러난다 — 그때는 실차 위다.
여기서 값으로 못박는다.

2026-08-06 하드웨어 변경으로 차동 구동 시험(`제자리 회전은 오른쪽이 전진` 등)은
폐기했다. 전륜 조향 차량은 제자리 회전을 **할 수 없고**, 그 명령은 거부돼야 한다.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# CI(alpine venv)는 패키지를 설치하지 않고 리포 루트에서 pytest 를 돌린다.
# 다른 패키지 시험(sentinel_bridge·mission)과 같은 관행이다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_drive.kinematics import (
    MODE_AUTO,
    MODE_MANUAL,
    REJECT_SPIN_IN_PLACE,
    VehicleLimits,
    drive_command,
    max_angular_radps,
    min_turning_radius_m,
    solve,
    steering_angle_rad,
    stop_command,
)

# 시험용 차량 한계. 노드 기본값(실측 전 잠정값)과 같은 수로 둔다.
#
# 실측 기하는 아래 `test_실측_기하의_최소회전반경` 이 따로 검증한다 — 이 상수를
# 실측으로 바꾸면 부호·포화 시험의 기대값이 전부 흔들리므로 분리했다.
L = 0.50
DELTA_MAX = math.radians(30.0)
LIMITS = VehicleLimits(
    wheelbase_m=L, max_steering_rad=DELTA_MAX, max_drive_mps=0.30, min_linear_mps=0.03
)


# ── 조향각 부호 — ω = v·tanδ/L 과 왕복 일치 ─────────────────────────────────

def test_직진은_조향각_0():
    delta, clamped = steering_angle_rad(0.25, 0.0, LIMITS)
    assert delta == 0.0
    assert clamped is False


def test_반시계는_좌조향_양수다():
    # REP-103: ω > 0 = 반시계 = 좌회전. 프로토콜 target_steering_mdeg 도 "+= 좌회전"
    # 이다. 여기가 뒤집히면 회전 명령에 로봇이 반대로 돈다.
    delta, _ = steering_angle_rad(0.25, 0.5, LIMITS)
    assert delta > 0


def test_시계는_우조향_음수다():
    delta, _ = steering_angle_rad(0.25, -0.5, LIMITS)
    assert delta < 0


def test_후진_반시계는_조향이_뒤집힌다():
    # 후진하며 반시계로 돌려면 앞바퀴를 우로 꺾어야 한다. 자전거 모델 식이
    # 그대로 처리하므로 후진에 특례를 두지 않는다 — 특례를 두면 그것이 버그다.
    delta, _ = steering_angle_rad(-0.25, 0.5, LIMITS)
    assert delta < 0


def test_정운동학과_왕복이_항등이다():
    # 역운동학 결과를 ω = v·tanδ/L 에 되넣으면 지령 ω 가 나와야 한다. 클램프에
    # 걸리지 않는 조합만 쓴다 — R_min=0.866m 이므로 v=0.2 에서 |ω| ≤ 0.23rad/s 다.
    for v, w in [(0.25, 0.0), (0.2, 0.15), (0.1, -0.05), (-0.15, 0.05)]:
        delta, clamped = steering_angle_rad(v, w, LIMITS)
        assert clamped is False, f'이 조합은 클램프되지 않아야 한다: v={v} ω={w}'
        assert v * math.tan(delta) / L == pytest.approx(w)


def test_델타맥스를_넘으면_클램프하고_알린다():
    # 저속 + 큰 ω 는 물리 한계에 걸린다. 조용히 자르면 실제 회두가 지령보다
    # 작은데 상위는 그것을 모른다 — 클램프 여부를 반환해 진단으로 올린다.
    delta, clamped = steering_angle_rad(0.05, 2.0, LIMITS)
    assert clamped is True
    assert delta == pytest.approx(DELTA_MAX)


def test_최소_회전반경은_L_over_tan_delta_max():
    assert min_turning_radius_m(LIMITS) == pytest.approx(L / math.tan(DELTA_MAX))


def test_각속도_상한은_선속도에_종속된다():
    # ω_max(v) = |v| / R_min. 정지에서 0 이 되는 것이 제자리 회전 불가의 수식 표현이다.
    r_min = min_turning_radius_m(LIMITS)
    assert max_angular_radps(0.30, LIMITS) == pytest.approx(0.30 / r_min)
    assert max_angular_radps(0.0, LIMITS) == 0.0


@pytest.mark.parametrize(
    'override',
    [
        {'wheelbase_m': 0.0},
        {'wheelbase_m': -0.5},
        {'max_steering_rad': 0.0},
        {'max_steering_rad': math.pi / 2.0},  # tan 이 발산해 R_min 이 0 이 된다
        {'max_drive_mps': 0.0},
        {'min_linear_mps': -0.01},
    ],
)
def test_한계값_검증(override):
    invalid = VehicleLimits(
        **{
            'wheelbase_m': L,
            'max_steering_rad': DELTA_MAX,
            'max_drive_mps': 0.30,
            'min_linear_mps': 0.03,
            **override,
        }
    )
    with pytest.raises(ValueError):
        invalid.validate()


# ── solve() — 후륜 속도·포화·거부 ───────────────────────────────────────────

def test_후륜은_좌우_같은_속도다():
    # 조향 링크가 회두를 정하므로 좌·우 속도 차로 회두를 보조하지 않는다(6.3).
    solution = solve(0.2, 0.4, LIMITS)
    command = drive_command(solution.speed_mps, solution.steering_rad)
    assert command['target_drive_left_mmps'] == command['target_drive_right_mmps'] == 200


def test_속도_상한에서_포화한다():
    solution = solve(1.5, 0.0, LIMITS)
    assert solution.speed_mps == pytest.approx(0.30)
    assert solution.accepted


def test_후진도_상한에서_포화한다():
    assert solve(-1.5, 0.0, LIMITS).speed_mps == pytest.approx(-0.30)


def test_포화_후_속도로_조향각을_계산한다():
    # 명세 §34-2 식이 δ = atan(L·ω / v_cmd) 이고 v_cmd 는 포화 후 값이다. 지령 ω 를
    # 지키는 쪽이며, 포화 전 v 를 쓰면 조향각이 작아져 실제 회두가 지령보다 작아진다.
    solution = solve(1.5, 0.3, LIMITS)
    expected, _ = steering_angle_rad(0.30, 0.3, LIMITS)
    assert solution.steering_rad == pytest.approx(expected)


def test_제자리_회전은_거부하고_조향을_유지한다():
    # 전륜 조향 차량은 v≈0 에서 회두하지 못한다(§34-2). 구동 0, 조향각은 마지막 값.
    solution = solve(0.0, 0.8, LIMITS, hold_steering_rad=0.2)
    assert solution.accepted is False
    assert solution.reject_reason == REJECT_SPIN_IN_PLACE
    assert solution.speed_mps == 0.0
    assert solution.steering_rad == pytest.approx(0.2)


def test_v_min_미만_저속_회두도_거부한다():
    # 0 이 아니라 v_min(0.03m/s) 이 경계다. "아주 조금 전진하면서 돌아라" 는
    # 타이어를 비틀 뿐 회두를 만들지 못한다.
    assert solve(0.02, 0.8, LIMITS).reject_reason == REJECT_SPIN_IN_PLACE
    assert solve(0.05, 0.8, LIMITS).accepted


def test_정지_명령은_거부가_아니다():
    # v≈0, ω=0 은 정지 명령으로 실행한다(조향각 유지).
    solution = solve(0.0, 0.0, LIMITS, hold_steering_rad=-0.1)
    assert solution.accepted
    assert solution.speed_mps == 0.0
    assert solution.steering_rad == pytest.approx(-0.1)


# ── JSON 계약 — esp32_motor_bridge._on_drive_command 가 받는 형태 ───────────

def test_계약_필수_필드와_단위_변환():
    command = drive_command(0.25, math.radians(-12.5), max_steering_rate_mdps=60000)
    assert command['mode'] == MODE_AUTO
    assert command['target_drive_left_mmps'] == 250
    assert command['target_drive_right_mmps'] == 250
    assert command['target_steering_mdeg'] == -12500  # 밀리도
    assert command['max_steering_rate_mdps'] == 60000
    assert command['command_timeout_ms'] == 300
    # 브리지가 int() 로 파싱하므로 전부 정수여야 한다. float 이 섞이면
    # int("250.0") 이 ValueError 로 명령이 조용히 버려진다.
    assert all(isinstance(v, int) for v in command.values())


def test_조향각_부호가_밀리도까지_보존된다():
    assert drive_command(0.1, math.radians(30.0))['target_steering_mdeg'] == 30000
    assert drive_command(0.1, math.radians(-30.0))['target_steering_mdeg'] == -30000


def test_반올림은_round_다():
    # int() 절단이면 0.6mm/s 와 0.6mdeg 가 0 이 된다 — 초저속·소각도에서 방향이 죽는다.
    assert drive_command(0.0006, 0.0)['target_drive_left_mmps'] == 1
    assert drive_command(-0.0006, 0.0)['target_drive_right_mmps'] == -1
    assert drive_command(0.0, math.radians(0.0006))['target_steering_mdeg'] == 1


def test_정지_명령은_조향각을_유지한다():
    # §34-7: 정지는 정차가 아니다. 0 을 실어 보내면 관성 주행 중 앞바퀴가 중립으로
    # 돌아가 궤적이 바뀐다.
    command = stop_command(steering_rad=math.radians(20.0))
    assert command['target_drive_left_mmps'] == 0
    assert command['target_drive_right_mmps'] == 0
    assert command['target_steering_mdeg'] == 20000
    assert command['mode'] == MODE_AUTO


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


def test_조향_한계는_펌웨어와_같은_값이다():
    # steering.cpp 의 STEERING_MAX_MDEG = 30000. 여기가 더 크면 Jetson 이 보낸
    # 명령을 펌웨어가 조용히 클램프하고 STEERING_COMMAND_INVALID 만 올라온다.
    assert round(math.degrees(DELTA_MAX) * 1000.0) == 30000


# ----------------------------------------------------------------------
# controlMode → DRIVE_COMMAND.mode 바이트 (S15P11A301-298)
#
# 수동 래치를 쥔 보드에 20Hz 로 mode=2 를 주장하면 초당 50회 다툰다. mode=1 은
# 래치와 합의하는 값이다.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ('control_mode', 'expected'),
    [
        ('MANUAL', MODE_MANUAL),
        ('AUTO', MODE_AUTO),
        # 모르는 값과 부재는 둘 다 기동 직후 현행 동작(AUTO)을 유지한다. 안전은
        # 이 바이트가 아니라 safety_gate 의 MISSION_STALE 이 담당한다.
        (None, MODE_AUTO),
        ('', MODE_AUTO),
        ('manual', MODE_AUTO),
        ('RETURNING', MODE_AUTO),
    ],
)
def test_모드_바이트_변환표(control_mode, expected):
    from sentinel_drive.kinematics import mode_byte

    assert mode_byte(control_mode) == expected


def test_모드_바이트_기본값을_바꿀_수_있다():
    from sentinel_drive.kinematics import MODE_SAFE_IDLE, mode_byte

    assert mode_byte(None, default=MODE_SAFE_IDLE) == MODE_SAFE_IDLE
    # 아는 값이면 default 를 보지 않는다.
    assert mode_byte('MANUAL', default=MODE_SAFE_IDLE) == MODE_MANUAL


# ── 실측 기하 (2026-08-06, S15P11A301-172) ───────────────────────────────────

# `sentinel_bringup/config/safety.yaml` 의 vehicle_kinematics 절과 같은 값이어야
# 한다. 두 곳이 어긋나면 이 시험이 먼저 깨진다.
MEASURED_WHEELBASE_M = 0.683
MEASURED_MAX_STEERING_DEG = 22.0


def test_실측_기하의_최소회전반경():
    """R_min 1.69m 를 못 박는다.

    종전 기본값(L=0.50·δ=30°)은 R_min 0.87m 를 뜻했고 그것은 **실제의 절반**이다.
    이 값이 작으면 자전거 모델이 δ = atan(L·ω/v) 를 작게 계산해 로봇이 지령보다
    덜 꺾는다 — 경로를 벗어나는데 로그에는 아무 오류가 없다. 그래서 수를 시험에
    남긴다.
    """
    measured = VehicleLimits(
        wheelbase_m=MEASURED_WHEELBASE_M,
        max_steering_rad=math.radians(MEASURED_MAX_STEERING_DEG),
        max_drive_mps=0.30,
        min_linear_mps=0.03,
    )

    assert min_turning_radius_m(measured) == pytest.approx(1.690, abs=0.005)

    # 잠정값과 2배 가까이 차이 난다는 사실 자체를 남긴다.
    assert min_turning_radius_m(measured) > min_turning_radius_m(LIMITS) * 1.9


def test_실측_기하에서_최대_각속도():
    """0.15m/s(첫 실기동 속도)에서 낼 수 있는 ω 상한.

    Nav2 가 이보다 큰 ω 를 지령하면 조향이 포화되고 실제 궤적이 계획과 벌어진다.
    """
    measured = VehicleLimits(
        wheelbase_m=MEASURED_WHEELBASE_M,
        max_steering_rad=math.radians(MEASURED_MAX_STEERING_DEG),
        max_drive_mps=0.30,
        min_linear_mps=0.03,
    )

    assert max_angular_radps(0.15, measured) == pytest.approx(0.15 / 1.690, abs=0.002)

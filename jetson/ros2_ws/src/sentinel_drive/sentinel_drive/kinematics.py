"""전륜 조향 역운동학 (S15P11A301-234·297, 명세 24.1·34-2).

`/cmd_vel`(v, ω)을 **후륜 목표 속도 + 전륜 목표 조향각**으로 바꾼다. rclpy 없이
시험한다.

## 2026-08-06 하드웨어 변경으로 차동 구동에서 되돌아왔다

앞쪽 캐스터 2개를 떼고 전륜 조향부를 복구했다. 전진·후진은 후륜 RS540 2개,
조향은 전륜 타이로드에 직결된 DS51150 서보 1개다. 즉 운동학이 차동 구동에서
**자전거 모델(Ackermann 근사)** 로 바뀌었고, 종전의 `left = v − ωW/2`는 폐기했다.
좌·우 후륜에는 **같은 속도**를 준다 — 조향각을 정한 링크와 좌·우 속도 차가
다투면 타이어·링키지에 무리가 간다(02장 6.3).

    v_cmd = clamp(v, ±v_max)
    δ     = clamp(atan(L·ω / v_cmd), ±δ_max)
    후륜 좌 = 후륜 우 = v_cmd

`v_cmd`(포화 후)를 분모로 쓰는 것은 명세 §34-2의 식 그대로다. 포화 전 `v`를 쓰면
곡률이 보존되는 대신 지령 `ω`가 작아진다 — 어느 쪽을 지키느냐의 선택이고 명세는
`ω` 를 지키는 쪽으로 정해져 있다.

## v≈0 에서 ω≠0 인 명령은 실행할 수 없다

전륜 조향 차량은 정지 상태에서 회두하지 못한다. 조향각을 최대로 꺾어도 차체가
전진하지 않으면 회두가 없다. 그래서 이 조합은 **거부하고**(구동 0, 조향각 유지)
진단으로 올린다(§34-2). 조용히 처리하면 "로봇이 왜 안 도나"를 매번 다시
조사하게 된다.

정지·거부 경로에서 조향을 중립으로 되돌리지 않는 이유는 정지가 곧 정차가 아니기
때문이다(§34-7). 급정지 순간에도 차량은 관성으로 더 가고, 그때 조향을 꺾으면
피하려던 장애물 쪽으로 밀려 나갈 수 있다.

## 부호

`δ > 0`이 좌회전(반시계)이고 `v > 0`에서 `ω = v·tanδ/L > 0`이다 — REP-103과
프로토콜 `target_steering_mdeg` 주석("+= 좌회전")이 같은 규약이다. 후진(`v < 0`)은
같은 식이 그대로 성립한다: `ω > 0`을 후진으로 만들려면 `δ < 0`이어야 하고
`atan(L·ω/v)`가 음수를 낸다.

여기서 부호를 틀리면 **화면에서는 보이지 않는다** — 회전 명령에 반대로 도는
것으로만 드러나며 그때는 실차 위다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 명세 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2
MODE_SAFE_IDLE = 0
MODE_MANUAL = 1
MODE_AUTO = 2


def mode_byte(control_mode: str | None, *, default: int = MODE_AUTO) -> int:
    """`state.schema.json` 의 `controlMode` → `DRIVE_COMMAND.mode` 바이트.

    수동 중에 젯슨이 `mode=2`(AUTO)를 20Hz 로 계속 주장하면 래치를 쥔 보드와 초당
    50회 다툰다. `mode=1` 은 래치와 **합의**하는 값이라 보드가 아무것도 되돌릴
    필요가 없고, 그 덕에 이 변경을 펌웨어보다 먼저 착지시켜도 안전하다.

    `default` 가 `MODE_SAFE_IDLE` 이 아닌 이유: `controlMode` 가 아직 안 온
    기동 직후에도 현행 벤치 동작(`mode=2`)을 유지한다. 안전은 이 바이트가 아니라
    `safety_gate` 가 담당한다 — 임무 상태를 모르면 `MISSION_STALE` 로 속도를 0 으로
    만든다. 여기서 `SAFE_IDLE` 을 내면 보호가 하나 더 생기는 것이 아니라 기동 직후
    조향이 중립으로 튀는 부작용만 생긴다.
    """
    if control_mode == 'MANUAL':
        return MODE_MANUAL
    if control_mode == 'AUTO':
        return MODE_AUTO
    return default


# 이 이하의 |ω|는 직진 명령으로 본다. v≈0 회두 거부 판정에도 같은 값을 쓴다.
ANGULAR_EPSILON_RADPS = 1e-3

# 0 나눗셈만 막는 값. v_min(거부 임계)과는 다른 것이다 — 그쪽은 물리 파라미터고
# 이쪽은 수치 안전장치다.
_SPEED_EPSILON_MPS = 1e-9

# solve() 거부 사유.
REJECT_SPIN_IN_PLACE = 'spin_in_place'


@dataclass(frozen=True)
class VehicleLimits:
    """차량 물리 한계. 전부 실측 대상이다(TBD-HW-008, §35-3).

    `wheelbase_m`은 전·후 차축 간격이고 `max_steering_rad`는 앞바퀴 실제 조향각의
    상한이다. 후자는 모터 ESP32의 `STEERING_MAX_MDEG`와 같은 값이어야 한다 —
    어긋나면 Jetson이 보낸 명령을 펌웨어가 조용히 클램프하는 구간이 생긴다.
    """

    wheelbase_m: float
    max_steering_rad: float
    max_drive_mps: float
    # 이 속도 미만에서는 회두 명령을 거부한다(§34-2).
    min_linear_mps: float = 0.03

    def validate(self) -> None:
        if self.wheelbase_m <= 0.0:
            raise ValueError(f'wheelbase_m 는 양수여야 한다: {self.wheelbase_m}')
        if not 0.0 < self.max_steering_rad < math.pi / 2.0:
            raise ValueError(
                f'max_steering_rad 는 (0, π/2) 안이어야 한다: {self.max_steering_rad}'
            )
        if self.max_drive_mps <= 0.0:
            raise ValueError(f'max_drive_mps 는 양수여야 한다: {self.max_drive_mps}')
        if self.min_linear_mps < 0.0:
            raise ValueError(f'min_linear_mps 는 음수일 수 없다: {self.min_linear_mps}')


@dataclass(frozen=True)
class DriveSolution:
    """후륜 속도 + 조향각. 거부된 명령은 `reject_reason`이 비어 있지 않다."""

    speed_mps: float
    steering_rad: float
    steering_clamped: bool = False
    reject_reason: str = ''

    @property
    def accepted(self) -> bool:
        return self.reject_reason == ''


def min_turning_radius_m(limits: VehicleLimits) -> float:
    """`R_min = L / tan(δ_max)`. Nav2 `minimum_turning_radius`에는 여기에 여유를 더해 넣는다(24.1)."""
    limits.validate()
    return limits.wheelbase_m / math.tan(limits.max_steering_rad)


def max_angular_radps(speed_mps: float, limits: VehicleLimits) -> float:
    """선속도에 종속된 각속도 상한 `|v| / R_min`.

    `δ` 클램프와 수학적으로 같은 제약이라 `solve()`가 따로 적용하지는 않는다.
    상위(24.2 속도 제한·게임패드 스케일링)가 미리 자를 때 쓰라고 노출한다.
    """
    return abs(speed_mps) / min_turning_radius_m(limits)


def steering_angle_rad(
    speed_mps: float, angular_radps: float, limits: VehicleLimits
) -> tuple[float, bool]:
    """`(δ, 클램프됐는지)`. `speed_mps`는 **포화 후** 선속도다(§34-2 식)."""
    limits.validate()
    if abs(speed_mps) < _SPEED_EPSILON_MPS:
        # 호출자가 v≈0 을 먼저 걸러야 한다. 여기서 0으로 나누지 않게만 막는다.
        return 0.0, False
    raw = math.atan(limits.wheelbase_m * angular_radps / speed_mps)
    clamped = max(-limits.max_steering_rad, min(limits.max_steering_rad, raw))
    return clamped, clamped != raw


def solve(
    linear_mps: float,
    angular_radps: float,
    limits: VehicleLimits,
    *,
    hold_steering_rad: float = 0.0,
) -> DriveSolution:
    """`(v, ω)` → 후륜 속도와 조향각.

    `hold_steering_rad`는 정지·거부 시 유지할 마지막 조향각이다(§34-7). 호출자가
    직전 해의 `steering_rad`를 그대로 넘긴다.
    """
    limits.validate()
    speed = max(-limits.max_drive_mps, min(limits.max_drive_mps, linear_mps))

    if abs(speed) < limits.min_linear_mps:
        if abs(angular_radps) > ANGULAR_EPSILON_RADPS:
            # 제자리 회전 시도. 구동도 조향도 바꾸지 않는다.
            return DriveSolution(
                speed_mps=0.0,
                steering_rad=hold_steering_rad,
                reject_reason=REJECT_SPIN_IN_PLACE,
            )
        # 정지 명령. 조향각은 마지막 값을 유지한다.
        return DriveSolution(speed_mps=0.0, steering_rad=hold_steering_rad)

    steering, clamped = steering_angle_rad(speed, angular_radps, limits)
    return DriveSolution(speed_mps=speed, steering_rad=steering, steering_clamped=clamped)


def drive_command(
    speed_mps: float,
    steering_rad: float,
    *,
    mode: int = MODE_AUTO,
    command_timeout_ms: int = 300,
    max_accel_mmps2: int = 0,
    max_steering_rate_mdps: int = 0,
) -> dict:
    """모터 브리지 JSON 계약(`esp32_motor_bridge_node._on_drive_command`)에 맞춘 dict.

    좌·우 후륜에 **같은 값**을 넣는다. 전자 차동 보정(TBD-CAL-002)이 붙으면 그때
    좌·우가 갈라지지만, 그것도 회두를 만들기 위한 것이 아니라 선회 중 스크럽을
    줄이기 위한 보정이다.

    mm/s·밀리도는 **round**로 정수화한다. int() 절단이면 저속·소각도에서 방향
    자체가 죽는다 — round(0.6)=1 대 int(0.6)=0 이 갈린다.
    """
    drive_mmps = round(speed_mps * 1000.0)
    return {
        'mode': mode,
        'target_drive_left_mmps': drive_mmps,
        'target_drive_right_mmps': drive_mmps,
        'target_steering_mdeg': round(math.degrees(steering_rad) * 1000.0),
        'max_accel_mmps2': max_accel_mmps2,
        'max_steering_rate_mdps': max_steering_rate_mdps,
        'command_timeout_ms': command_timeout_ms,
    }


def stop_command(
    *,
    steering_rad: float = 0.0,
    mode: int = MODE_AUTO,
    max_steering_rate_mdps: int = 0,
) -> dict:
    """정지 명령. `/cmd_vel`이 끊겼을 때와 종료 시에 쓴다.

    조향각은 **인자로 받은 마지막 값을 그대로 실어 보낸다**(§34-7). 0을 보내면
    관성 주행 중에 앞바퀴가 중립으로 돌아가 궤적이 바뀐다.
    """
    return drive_command(
        0.0,
        steering_rad,
        mode=mode,
        max_steering_rate_mdps=max_steering_rate_mdps,
    )

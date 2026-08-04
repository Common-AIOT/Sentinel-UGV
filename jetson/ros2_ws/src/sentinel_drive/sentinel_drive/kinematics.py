"""차동 구동 역운동학 (S15P11A301-234, 명세 24.1·34-2).

`/cmd_vel`(v, ω)을 좌·우 바퀴 선속도로 바꾼다. rclpy 없이 시험한다.

## 부호 규약은 정운동학이 근거다

`esp32_bridge/wheel_odometry.py`가 이렇게 적립한다.

    delta_yaw = (right_distance - left_distance) / track_width

즉 **반시계(ω > 0, REP-103)는 오른쪽 바퀴가 더 빠르다.** 역은 그대로 뒤집어

    left  = v − ω·W/2
    right = v + ω·W/2

이다. 여기서 부호를 틀리면 오도메트리가 주행과 반대로 적립되는데, SLAM 의
`map→odom` 보정이 그것을 덮어서 **화면에서는 안 보인다** — 회전 명령을 줬을 때
반대로 도는 것으로만 드러난다.

바퀴 반경은 쓰지 않는다. 브리지 계약(`target_drive_*_mmps`)이 바퀴 **선속도**라
각속도 변환이 필요 없다 — 반경은 ESP32 쪽(tick→거리)의 몫이다.
"""

from __future__ import annotations

# 명세 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2
MODE_SAFE_IDLE = 0
MODE_MANUAL = 1
MODE_AUTO = 2


def wheel_speeds_mps(
    linear_mps: float, angular_radps: float, track_width_m: float
) -> tuple[float, float]:
    """(v, ω) → (left, right) 바퀴 선속도(m/s)."""
    if track_width_m <= 0.0:
        raise ValueError(f'track_width_m 는 양수여야 한다: {track_width_m}')
    half = angular_radps * track_width_m / 2.0
    return linear_mps - half, linear_mps + half


def saturate(
    left_mps: float, right_mps: float, max_wheel_mps: float
) -> tuple[float, float]:
    """상한을 넘으면 **양쪽을 같은 비율로** 줄인다.

    한쪽만 자르면 좌·우 차이(= 지령한 곡률)가 바뀌어 로봇이 다른 방향으로
    간다. 비율을 유지하면 느려질 뿐 같은 호를 그린다. Nav2 컨트롤러는 자기가
    보낸 곡률대로 움직인다고 가정하므로, 여기서 곡률을 바꾸면 컨트롤러가
    보정하려다 진동한다.
    """
    if max_wheel_mps <= 0.0:
        raise ValueError(f'max_wheel_mps 는 양수여야 한다: {max_wheel_mps}')
    peak = max(abs(left_mps), abs(right_mps))
    if peak <= max_wheel_mps:
        return left_mps, right_mps
    scale = max_wheel_mps / peak
    return left_mps * scale, right_mps * scale


def drive_command(
    left_mps: float,
    right_mps: float,
    *,
    mode: int = MODE_AUTO,
    command_timeout_ms: int = 300,
    max_accel_mmps2: int = 0,
) -> dict:
    """모터 브리지 JSON 계약(`esp32_motor_bridge_node._on_drive_command`)에 맞춘 dict.

    mm/s 는 **round** 로 정수화한다. int() 절단이면 0.9996m/s 지령이 999mm/s 가
    되는 정도지만, 저속(수 mm/s)에서는 방향까지 죽는다 — round(-0.4)=0 대
    int(-0.4)=0 은 같아도 round(0.6)=1 대 int(0.6)=0 이 갈린다.
    """
    return {
        'mode': mode,
        'target_drive_left_mmps': round(left_mps * 1000.0),
        'target_drive_right_mmps': round(right_mps * 1000.0),
        'command_timeout_ms': command_timeout_ms,
        'max_accel_mmps2': max_accel_mmps2,
    }


def stop_command(*, mode: int = MODE_AUTO) -> dict:
    """정지 명령. `/cmd_vel` 이 끊겼을 때와 종료 시에 쓴다."""
    return drive_command(0.0, 0.0, mode=mode)

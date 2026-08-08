"""ESP32 직렬 송신 직전의 주행 명령 안전 한계.

상위 ``vehicle_kinematics`` 도 자율주행 명령을 제한하지만, 다른 발행자가
``/esp32_motor_bridge/drive_command`` 에 직접 쓰거나 상위 노드에 결함이 생기면 그
제한을 우회할 수 있다. 이 모듈은 모터 보드로 나가는 마지막 Jetson 경계에서 좌·우
주행 속도와 조향각을 다시 포화시킨다.

ROS 의존성을 두지 않아 하드웨어 없이 경계값을 단위 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass


# DRIVE_COMMAND 의 세 필드는 모두 little-endian signed int16(`hhh`)이다.
_SIGNED_INT16_MAX = 32_767


@dataclass(frozen=True)
class DriveCommandLimits:
    """좌·우 대칭으로 적용할 최종 액추에이터 명령 한계."""

    max_drive_mmps: int
    max_steering_mdeg: int

    def __post_init__(self) -> None:
        for name, value in (
            ("max_drive_mmps", self.max_drive_mmps),
            ("max_steering_mdeg", self.max_steering_mdeg),
        ):
            if not 0 < value <= _SIGNED_INT16_MAX:
                raise ValueError(
                    f"{name} 는 1..{_SIGNED_INT16_MAX} 범위여야 한다: {value}"
                )


@dataclass(frozen=True)
class FilteredDriveTargets:
    """한계가 적용된 값과 변경된 필드 이름."""

    left_mmps: int
    right_mmps: int
    steering_mdeg: int
    filtered_fields: tuple[str, ...] = ()

    @property
    def was_filtered(self) -> bool:
        return bool(self.filtered_fields)


def _clamp_symmetric(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))


def filter_drive_targets(
    *,
    left_mmps: int,
    right_mmps: int,
    steering_mdeg: int,
    limits: DriveCommandLimits,
) -> FilteredDriveTargets:
    """속도·조향 초과값을 각각의 가장 가까운 한계값으로 포화한다."""

    filtered_left = _clamp_symmetric(left_mmps, limits.max_drive_mmps)
    filtered_right = _clamp_symmetric(right_mmps, limits.max_drive_mmps)
    filtered_steering = _clamp_symmetric(steering_mdeg, limits.max_steering_mdeg)

    fields: list[str] = []
    if filtered_left != left_mmps:
        fields.append("target_drive_left_mmps")
    if filtered_right != right_mmps:
        fields.append("target_drive_right_mmps")
    if filtered_steering != steering_mdeg:
        fields.append("target_steering_mdeg")

    return FilteredDriveTargets(
        left_mmps=filtered_left,
        right_mmps=filtered_right,
        steering_mdeg=filtered_steering,
        filtered_fields=tuple(fields),
    )

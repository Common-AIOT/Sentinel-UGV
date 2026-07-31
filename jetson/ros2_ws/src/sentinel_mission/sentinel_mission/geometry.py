"""지도 좌표 계산 (S15P11A301-170).

ROS를 모르는 순수 계산만 둔다. CI가 rclpy 없이 시험할 수 있어야 한다 —
`mission_state`·`message_mapper`와 같은 원칙이다.

`yaw_from_quaternion`은 sentinel_bridge.message_mapper에도 있지만 import하지
않는다. 두 패키지는 토픽으로만 만나고, 한쪽을 고칠 때 다른 쪽이 깨지는 경로를
만들지 않는다. 4줄짜리 수학의 중복이 패키지 결합보다 싸다.
"""

from __future__ import annotations

import math
from typing import Any


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 z축 회전(yaw, 라디안)을 꺼낸다.

    평면 주행이므로 roll·pitch는 쓰지 않는다(23.2). 표준 ZYX 오일러 변환의
    yaw 항이다.
    """
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def encounter_pose(
    translation_x: float,
    translation_y: float,
    rotation: tuple[float, float, float, float],
    map_id: str | None = None,
) -> dict[str, Any]:
    """encounter.schema.json의 pose 형태로 만든다.

    소수 3자리로 자른다. 5cm 해상도 지도(S15P11A301-137)에서 밀리미터 이하는
    의미가 없고, 자르지 않으면 부동소수 꼬리가 로그와 DB를 어지럽힌다.

    `mapId`는 지도 저장·등록(S15P11A301-171)이 수명주기를 확정하기 전까지
    null이다. mission_manager가 자체 UUID를 만들면 cloud_bridge telemetry의
    mapId와 어긋난 값 두 개가 생긴다.
    """
    qx, qy, qz, qw = rotation
    return {
        'x': round(float(translation_x), 3),
        'y': round(float(translation_y), 3),
        'yaw': round(yaw_from_quaternion(qx, qy, qz, qw), 4),
        'mapId': map_id,
    }

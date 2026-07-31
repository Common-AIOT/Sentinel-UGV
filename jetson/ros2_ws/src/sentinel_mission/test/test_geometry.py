"""encounter 위치 계산 시험 (S15P11A301-170).

`geometry`는 ROS를 모르므로 CI에서 돈다. TF 조회 자체는 rclpy가 필요해 실기기
검증으로 본다 — 여기서는 좌표 변환의 수학과 스키마 계약을 고정한다.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_mission.geometry import encounter_pose, yaw_from_quaternion  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_DIR = REPO_ROOT / 'common' / 'schemas'


@pytest.mark.parametrize(
    'quaternion,expected_yaw',
    [
        # (x, y, z, w) → yaw. z축 회전 θ의 쿼터니언은 (0, 0, sin θ/2, cos θ/2)다.
        ((0.0, 0.0, 0.0, 1.0), 0.0),
        ((0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)), math.pi / 2),
        ((0.0, 0.0, 1.0, 0.0), math.pi),
        ((0.0, 0.0, -math.sin(math.pi / 4), math.cos(math.pi / 4)), -math.pi / 2),
    ],
)
def test_yaw_from_quaternion_known_rotations(quaternion, expected_yaw):
    """알려진 z축 회전에서 yaw가 정확해야 한다.

    이 값이 틀리면 관제 지도의 로봇 방향 화살표가 엉뚱한 곳을 가리키고,
    S15P11A301-137에서 실측한 SLAM 좌표와도 어긋난다.
    """
    x, y, z, w = quaternion
    assert yaw_from_quaternion(x, y, z, w) == pytest.approx(expected_yaw, abs=1e-9)


def test_yaw_matches_bridge_implementation_convention():
    """cloud_bridge의 yaw와 같은 규약(ZYX, 라디안, atan2 범위)이어야 한다.

    두 노드가 같은 로봇의 위치를 각각 내보낸다 — telemetry(bridge)와
    encounter(mission). 규약이 다르면 관제 지도에서 로봇과 발견 지점의 방향이
    서로 안 맞는다. 일부러 import하지 않고 값으로 고정한다(패키지 결합 금지).
    """
    quaternion = (0.1, 0.2, 0.3, 0.9273618495495703)  # 임의의 정규화된 회전
    siny = 2.0 * (quaternion[3] * quaternion[2] + quaternion[0] * quaternion[1])
    cosy = 1.0 - 2.0 * (quaternion[1] ** 2 + quaternion[2] ** 2)
    assert yaw_from_quaternion(*quaternion) == pytest.approx(
        math.atan2(siny, cosy)
    )


def test_encounter_pose_satisfies_the_schema():
    """만드는 pose가 encounter.schema.json의 pose 부분 스키마를 통과해야 한다.

    형식이 틀리면 계약 검증(CI)이 아니라 실기기에서 mission_manager 발행이
    스키마 위반이 되고, 백엔드 EncounterWriter가 조용히 map_x/y/yaw를 버린다.
    """
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads(
        (SCHEMA_DIR / 'encounter.schema.json').read_text(encoding='utf-8')
    )['properties']['pose']

    pose = encounter_pose(1.23456, -0.98765, (0.0, 0.0, 0.7071, 0.7071))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(pose))
    assert not errors, [error.message for error in errors]


def test_encounter_pose_rounds_to_map_resolution():
    """소수 3자리로 자른다. 5cm 해상도 지도에서 그 이하는 잡음이다."""
    pose = encounter_pose(1.23456789, 2.98765432, (0.0, 0.0, 0.0, 1.0))
    assert pose['x'] == 1.235
    assert pose['y'] == 2.988
    assert pose['yaw'] == 0.0


def test_map_id_defaults_to_null():
    """mapId는 지도 등록(S15P11A301-171)이 수명주기를 정하기 전까지 null이다.

    mission_manager가 자체 UUID를 만들면 cloud_bridge telemetry의 mapId와
    어긋난 값 두 개가 생긴다.
    """
    pose = encounter_pose(0.0, 0.0, (0.0, 0.0, 0.0, 1.0))
    assert pose['mapId'] is None

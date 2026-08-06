"""후륜 엔코더 wheel odometry 시험 (Phase 0).

여기서 검증하는 것은 **적분 수학**이다(tick 차분·원호 적분·랩어라운드). 실차에서
`angular_z` 를 신뢰할 수 있느냐는 별개 문제이고, 전륜 조향 복구 이후로는 신뢰하지
않는다 — 후륜 스크럽 때문이며 yaw 는 IMU 가 담당한다(§35-3, `wheel_odometry` docstring).
좌·우 속도 차를 직접 주는 아래 시험들은 그 수학을 고정하기 위한 것이지 실제 기동을
흉내 내는 것이 아니다.

`packet_codec` 테스트와 같이 rclpy를 import하지 않으므로 ROS 없이 돈다.
숫자는 전부 캘리브레이션 값과 무관하게 검증할 수 있도록, 계산하기 쉬운
가짜 스케일(1 tick = 1mm, W = 1m)로 잡았다.
"""

from __future__ import annotations

import dataclasses
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.wheel_odometry import (  # noqa: E402
    REJECT_BASELINE,
    REJECT_DT_NOT_POSITIVE,
    REJECT_SPEED_IMPLAUSIBLE,
    Pose2D,
    WheelOdometry,
    WheelOdometryConfig,
    default_meters_per_tick,
    int32_delta,
    normalize_angle,
    uint32_delta_ms,
    yaw_to_quaternion,
)

MM_PER_TICK = 0.001
TRACK_WIDTH_M = 1.0


def make_odometry(**overrides) -> WheelOdometry:
    config = WheelOdometryConfig(
        meters_per_tick_left=MM_PER_TICK,
        meters_per_tick_right=MM_PER_TICK,
        track_width_m=TRACK_WIDTH_M,
        max_wheel_speed_mps=2.0,
    )
    return WheelOdometry(dataclasses.replace(config, **overrides))


# ---- 랩어라운드 ----


@pytest.mark.parametrize(
    ("new_value", "old_value", "expected"),
    [
        (100, 40, 60),
        (40, 100, -60),
        (-2147483648, 2147483647, 1),  # int32 양수 끝 -> 음수 끝
        (2147483647, -2147483648, -1),
        (0, 0, 0),
    ],
)
def test_int32_delta_handles_wraparound(new_value, old_value, expected):
    assert int32_delta(new_value, old_value) == expected


def test_uint32_delta_ms_handles_wraparound():
    assert uint32_delta_ms(5, 0xFFFFFFFF) == 6
    assert uint32_delta_ms(120, 100) == 20


@pytest.mark.parametrize(
    ("angle", "expected"),
    [(0.0, 0.0), (math.pi, math.pi), (-math.pi, math.pi), (3 * math.pi, math.pi)],
)
def test_normalize_angle(angle, expected):
    assert normalize_angle(angle) == pytest.approx(expected)


def test_normalize_angle_folds_beyond_two_pi():
    assert normalize_angle(2 * math.pi + 0.5) == pytest.approx(0.5)
    assert normalize_angle(-2 * math.pi - 0.5) == pytest.approx(-0.5)


# ---- 기준점·거부 ----


def test_first_sample_only_stores_baseline():
    odometry = make_odometry()
    result = odometry.update(1000, 1000, 0)
    assert not result.accepted
    assert result.reason == REJECT_BASELINE
    assert odometry.pose == Pose2D()


def test_zero_dt_keeps_baseline_so_motion_is_not_lost():
    odometry = make_odometry()
    odometry.update(0, 0, 100)

    # 같은 uptime으로 두 번 오면 그 프레임은 버리되 기준점은 남긴다.
    rejected = odometry.update(50, 50, 100)
    assert rejected.reason == REJECT_DT_NOT_POSITIVE

    # 다음 프레임이 0->100 tick 구간을 통째로 덮으므로 이동량이 유실되지 않는다.
    accepted = odometry.update(100, 100, 200)
    assert accepted.accepted
    assert accepted.sample.pose.x == pytest.approx(0.1)
    assert accepted.sample.dt_s == pytest.approx(0.1)


def test_implausible_jump_is_rejected_and_resyncs_baseline():
    odometry = make_odometry()
    odometry.update(0, 0, 0)

    # 20ms에 10m는 500m/s - I2C 글리치다.
    rejected = odometry.update(10_000_000, 10_000_000, 20)
    assert rejected.reason == REJECT_SPEED_IMPLAUSIBLE
    assert odometry.rejected_sample_count == 1
    assert odometry.pose == Pose2D()

    # 기준점이 글리치 값으로 재동기화되므로 다음 정상 샘플이 다시 막히지 않는다.
    accepted = odometry.update(10_000_020, 10_000_020, 40)
    assert accepted.accepted
    assert odometry.pose.x == pytest.approx(0.020)


# ---- 정운동학 ----


def test_straight_line_accumulates_distance_without_rotation():
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    sample = odometry.update(1000, 1000, 1000).sample

    assert sample.pose.x == pytest.approx(1.0)
    assert sample.pose.y == pytest.approx(0.0)
    assert sample.pose.yaw == pytest.approx(0.0)
    assert sample.linear_x == pytest.approx(1.0)
    assert sample.angular_z == pytest.approx(0.0)
    assert sample.dt_s == pytest.approx(1.0)


def test_reverse_is_symmetric():
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    sample = odometry.update(-500, -500, 1000).sample

    assert sample.pose.x == pytest.approx(-0.5)
    assert sample.linear_x == pytest.approx(-0.5)


def test_spin_in_place_rotates_without_translating():
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    # d_R - d_L = 1.0m, W = 1.0m -> dyaw = 1.0 rad, d_center = 0
    sample = odometry.update(-500, 500, 1000).sample

    assert sample.pose.x == pytest.approx(0.0, abs=1e-12)
    assert sample.pose.y == pytest.approx(0.0, abs=1e-12)
    assert sample.pose.yaw == pytest.approx(1.0)
    assert sample.linear_x == pytest.approx(0.0, abs=1e-12)
    assert sample.angular_z == pytest.approx(1.0)


def test_left_wheel_faster_turns_counterclockwise():
    """REP-103: +yaw는 반시계(좌회전)다. 우측 바퀴가 빠르면 좌회전이어야 한다."""
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    sample = odometry.update(500, 1000, 1000).sample

    assert sample.angular_z > 0.0
    assert sample.pose.y > 0.0  # 좌회전이므로 좌측(+y)으로 휜다


def test_quarter_turn_arc_matches_closed_form():
    """반지름 1m 원호로 90도 회전. 원호 적분이 정확한지 본다."""
    odometry = make_odometry()
    odometry.update(0, 0, 0)

    radius = 1.0
    delta_yaw = math.pi / 2
    center_distance = radius * delta_yaw
    # d_R - d_L = dyaw * W, (d_R + d_L)/2 = center_distance
    right_distance = center_distance + 0.5 * delta_yaw * TRACK_WIDTH_M
    left_distance = center_distance - 0.5 * delta_yaw * TRACK_WIDTH_M

    sample = odometry.update(
        round(left_distance / MM_PER_TICK), round(right_distance / MM_PER_TICK), 4000
    ).sample

    # (0,0,0)에서 좌회전 반지름 1m 원호 90도 -> (1, 1, pi/2)
    assert sample.pose.x == pytest.approx(radius, abs=1e-3)
    assert sample.pose.y == pytest.approx(radius, abs=1e-3)
    assert sample.pose.yaw == pytest.approx(delta_yaw, abs=1e-3)


def test_track_width_scales_angular_velocity():
    """W가 각속도 정확도를 지배한다 - 절반이면 각속도는 두 배."""
    wide = make_odometry(track_width_m=1.0)
    narrow = make_odometry(track_width_m=0.5)
    for odometry in (wide, narrow):
        odometry.update(0, 0, 0)

    wide_sample = wide.update(-500, 500, 1000).sample
    narrow_sample = narrow.update(-500, 500, 1000).sample
    assert narrow_sample.angular_z == pytest.approx(2 * wide_sample.angular_z)


def test_left_right_scale_mismatch_produces_drift():
    """좌·우 스케일 편차(±3%p 합격 기준)가 직진 명령에서 yaw 드리프트로 나타난다."""
    odometry = make_odometry(meters_per_tick_right=MM_PER_TICK * 1.03)
    odometry.update(0, 0, 0)
    sample = odometry.update(1000, 1000, 1000).sample

    assert sample.angular_z > 0.0
    assert sample.pose.yaw == pytest.approx(0.03 / TRACK_WIDTH_M, rel=1e-6)


# ---- 재부팅·pose 연속성 ----


def test_encoder_origin_reset_keeps_pose():
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    odometry.update(1000, 1000, 1000)
    assert odometry.pose.x == pytest.approx(1.0)

    # 보드 재부팅: tick·uptime이 0으로 돌아간다.
    odometry.reset_encoder_origin()
    assert odometry.encoder_origin_reset_count == 1
    assert odometry.pose.x == pytest.approx(1.0)  # odom은 연속이어야 한다

    assert odometry.update(0, 0, 0).reason == REJECT_BASELINE
    assert odometry.pose.x == pytest.approx(1.0)

    # 재부팅 이후 이동은 기존 pose에 이어서 쌓인다.
    odometry.update(500, 500, 1000)
    assert odometry.pose.x == pytest.approx(1.5)


def test_reset_pose():
    odometry = make_odometry()
    odometry.update(0, 0, 0)
    odometry.update(1000, 1000, 1000)
    odometry.reset_pose()
    assert odometry.pose == Pose2D()


# ---- 설정 검증 ----


@pytest.mark.parametrize(
    "overrides",
    [
        {"track_width_m": 0.0},
        {"track_width_m": -0.3},
        {"meters_per_tick_left": 0.0},
        {"meters_per_tick_right": -1e-7},
        {"max_wheel_speed_mps": 0.0},
    ],
)
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(ValueError):
        make_odometry(**overrides)


# ---- 보조 함수 ----


def test_default_meters_per_tick_matches_sensor_task_constants():
    # (pi * 0.120) / (16384 * 82)
    assert default_meters_per_tick(0.120, 16384, 82.0) == pytest.approx(2.806e-07, rel=1e-3)


def test_yaw_to_quaternion_is_unit_and_matches_known_angles():
    assert yaw_to_quaternion(0.0) == pytest.approx((0.0, 0.0, 0.0, 1.0))
    x, y, z, w = yaw_to_quaternion(math.pi / 2)
    assert (x, y) == (0.0, 0.0)
    assert z == pytest.approx(math.sqrt(0.5))
    assert w == pytest.approx(math.sqrt(0.5))
    assert x * x + y * y + z * z + w * w == pytest.approx(1.0)

"""후륜 엔코더 wheel odometry 데드레커닝 (Phase 0, 명세 23.2·§35-3).

센서 ESP32의 `ENCODER_STATE`(후륜 좌·우 누적 tick + senderUptimeMs)를 받아
정운동학으로 `(v, ω)`와 누적 pose를 구한다.

    d_L = Δtick_L · meters_per_tick_L
    d_R = Δtick_R · meters_per_tick_R
    d   = (d_R + d_L) / 2 ,  Δθ = (d_R − d_L) / W

## yaw 는 이 모듈을 신뢰하지 않는다 (2026-08-06 전륜 조향 복구)

`v = (d_R + d_L)/2` 는 구조와 무관하게 성립하지만 `Δθ` 는 그렇지 않다. 전륜 조향
차량에서는 조향 링크가 회두를 정하고 후륜은 같은 속도로 구동되므로, 선회 중 내·외측
후륜이 노면에 **스크럽**한다 - 좌·우 속도 차가 기하와 맞지 않으며 부호만 겨우 맞는
수준이다(§35-3). 그래서 **yaw 의 주 소스는 IMU 자이로**이고 `ekf_node`가 엔코더
`vx` + IMU `vyaw` 로 융합한다(23.2가 엔코더 `vyaw` 를 EKF 입력에서 아예 뺀 근거가
이 구조에서 더 강해졌다).

여기서 `Δθ` 를 계속 계산하는 이유는 `x·y` 적분에 자세가 필요하기 때문이고, 이
모듈이 내는 `angular_z` 는 EKF 입력이 아니라 §35-3의 비교 측정(엔코더 yaw · 조향각
yaw · IMU yaw · 실제 회전량)용 값이다.

`packet_codec`처럼 `rclpy`를 import하지 않는 순수 로직이라 ROS 없이 pytest로
검증할 수 있고, Phase 1에서 `sentinel_drive`가 그대로 가져다 쓸 수 있다.

설계 결정 두 가지:

- **속도를 보드가 보낸 mm/s가 아니라 tick 차분에서 구한다.** 보드의 mm/s는
  `sensor_task.cpp`에 하드코딩된 기어비·바퀴 지름(§35-3 실측 전 임시값)으로
  계산되므로, 캘리브레이션 값을 한 곳(Jetson 파라미터)에만 두려면 tick이
  유일한 입력이어야 한다. 재플래싱 없이 재튜닝할 수 있다는 실무적 이점도 크다.
- **dt를 프레임 도착 시각이 아니라 `senderUptimeMs`에서 구한다.** USB 직렬
  도착 지터가 그대로 속도 잡음이 되는 것을 막는다(ESP32 monotonic 시계).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_INT32_HALF_SPAN = 1 << 31
_UINT32_MASK = 0xFFFFFFFF

# 이 값보다 |Δθ|가 작으면 원호 대신 직선으로 적분한다(0 나눗셈 회피).
_STRAIGHT_LINE_YAW_EPSILON = 1e-9

# update() 거부 사유. 빈 문자열이 아니면 sample은 None이다.
REJECT_BASELINE = "baseline"  # 첫 샘플 - 기준점만 잡고 적분하지 않는다
REJECT_DT_NOT_POSITIVE = "dt_not_positive"
REJECT_SPEED_IMPLAUSIBLE = "speed_implausible"


def normalize_angle(angle_rad: float) -> float:
    """(-pi, pi] 범위로 접는다."""
    wrapped = math.fmod(angle_rad + math.pi, 2.0 * math.pi)
    if wrapped <= 0.0:
        wrapped += 2.0 * math.pi
    return wrapped - math.pi


def int32_delta(new_value: int, old_value: int) -> int:
    """int32 누적 카운터의 랩어라운드-세이프 차분.

    ESP32의 `accumulatedTicks`는 int32다. 기본 캘리브레이션값 기준 바퀴
    1회전이 약 134만 tick이라 600m 남짓 주행하면 실제로 넘친다 - 이론상
    걱정이 아니라 한 번의 임무 안에서 일어날 수 있는 일이다.
    """
    return ((new_value - old_value + _INT32_HALF_SPAN) & _UINT32_MASK) - _INT32_HALF_SPAN


def uint32_delta_ms(new_value: int, old_value: int) -> int:
    """uint32 밀리초 시계의 랩어라운드-세이프 차분(약 49.7일 주기)."""
    return (new_value - old_value) & _UINT32_MASK


@dataclass(frozen=True)
class WheelOdometryConfig:
    """§35-3 실측으로 채워야 하는 값들(TBD-CAL-001)."""

    meters_per_tick_left: float
    meters_per_tick_right: float
    track_width_m: float
    # tick 점프·I2C 노이즈로 생긴 비현실적 샘플을 걸러낸다. 실제 최고 속도보다
    # 넉넉히 크게 두어야 정상 주행을 잘라먹지 않는다.
    max_wheel_speed_mps: float = 2.0

    def validate(self) -> None:
        if self.track_width_m <= 0.0:
            raise ValueError(f"track_width_m must be positive: {self.track_width_m}")
        if self.meters_per_tick_left <= 0.0 or self.meters_per_tick_right <= 0.0:
            raise ValueError("meters_per_tick_left/right must be positive")
        if self.max_wheel_speed_mps <= 0.0:
            raise ValueError(f"max_wheel_speed_mps must be positive: {self.max_wheel_speed_mps}")


@dataclass(frozen=True)
class Pose2D:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class OdometrySample:
    pose: Pose2D
    linear_x: float
    angular_z: float
    dt_s: float
    left_distance_m: float
    right_distance_m: float


@dataclass(frozen=True)
class UpdateResult:
    sample: OdometrySample | None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.sample is not None


class WheelOdometry:
    """누적 tick을 받아 odom pose와 `(v, ω)`를 낸다.

    스레드 안전하지 않다 - 브리지 노드의 단일 RX 스레드에서만 호출한다.
    """

    def __init__(self, config: WheelOdometryConfig) -> None:
        config.validate()
        self._config = config
        self._pose = Pose2D()
        self._previous_ticks_left: int | None = None
        self._previous_ticks_right: int | None = None
        self._previous_uptime_ms: int | None = None
        self.rejected_sample_count = 0
        self.encoder_origin_reset_count = 0

    @property
    def config(self) -> WheelOdometryConfig:
        return self._config

    @property
    def pose(self) -> Pose2D:
        return self._pose

    def reset_encoder_origin(self) -> None:
        """보드 재부팅 등으로 tick 카운터가 0으로 돌아갔을 때 호출한다.

        pose는 **유지한다.** odom 프레임은 연속이어야 하므로(REP-105) 보드가
        재부팅했다고 로봇이 원점으로 순간이동해서는 안 된다. 재부팅 동안의
        이동량만 조용히 유실된다.
        """
        self._previous_ticks_left = None
        self._previous_ticks_right = None
        self._previous_uptime_ms = None
        self.encoder_origin_reset_count += 1

    def reset_pose(self, pose: Pose2D | None = None) -> None:
        self._pose = pose if pose is not None else Pose2D()

    def update(self, ticks_left: int, ticks_right: int, uptime_ms: int) -> UpdateResult:
        if (
            self._previous_ticks_left is None
            or self._previous_ticks_right is None
            or self._previous_uptime_ms is None
        ):
            self._store_baseline(ticks_left, ticks_right, uptime_ms)
            return UpdateResult(None, REJECT_BASELINE)

        dt_s = uint32_delta_ms(uptime_ms, self._previous_uptime_ms) / 1000.0
        if dt_s <= 0.0:
            # 기준점을 갱신하지 않는다 - 다음 프레임이 더 긴 구간을 덮게 두면
            # 이 프레임의 이동량이 유실되지 않는다.
            self.rejected_sample_count += 1
            return UpdateResult(None, REJECT_DT_NOT_POSITIVE)

        delta_left = int32_delta(ticks_left, self._previous_ticks_left)
        delta_right = int32_delta(ticks_right, self._previous_ticks_right)
        left_distance_m = delta_left * self._config.meters_per_tick_left
        right_distance_m = delta_right * self._config.meters_per_tick_right

        limit_m = self._config.max_wheel_speed_mps * dt_s
        if abs(left_distance_m) > limit_m or abs(right_distance_m) > limit_m:
            # 여기서는 기준점을 **갱신한다.** 글리치 tick을 버린 채 옛 기준점을
            # 남기면 다음 프레임에서 같은 점프가 다시 계산돼 영구히 막힌다.
            self._store_baseline(ticks_left, ticks_right, uptime_ms)
            self.rejected_sample_count += 1
            return UpdateResult(None, REJECT_SPEED_IMPLAUSIBLE)

        self._store_baseline(ticks_left, ticks_right, uptime_ms)

        center_distance_m = 0.5 * (left_distance_m + right_distance_m)
        delta_yaw = (right_distance_m - left_distance_m) / self._config.track_width_m
        self._pose = _integrate(self._pose, center_distance_m, delta_yaw)

        return UpdateResult(
            OdometrySample(
                pose=self._pose,
                linear_x=center_distance_m / dt_s,
                angular_z=delta_yaw / dt_s,
                dt_s=dt_s,
                left_distance_m=left_distance_m,
                right_distance_m=right_distance_m,
            )
        )

    def _store_baseline(self, ticks_left: int, ticks_right: int, uptime_ms: int) -> None:
        self._previous_ticks_left = ticks_left
        self._previous_ticks_right = ticks_right
        self._previous_uptime_ms = uptime_ms


def _integrate(pose: Pose2D, center_distance_m: float, delta_yaw: float) -> Pose2D:
    """원호(exact arc) 적분. 제자리 회전·급선회에서 중점 근사보다 정확하다."""
    if abs(delta_yaw) < _STRAIGHT_LINE_YAW_EPSILON:
        return Pose2D(
            x=pose.x + center_distance_m * math.cos(pose.yaw),
            y=pose.y + center_distance_m * math.sin(pose.yaw),
            yaw=pose.yaw,
        )

    radius = center_distance_m / delta_yaw
    next_yaw = pose.yaw + delta_yaw
    return Pose2D(
        x=pose.x + radius * (math.sin(next_yaw) - math.sin(pose.yaw)),
        y=pose.y + radius * (math.cos(pose.yaw) - math.cos(next_yaw)),
        yaw=normalize_angle(next_yaw),
    )


def yaw_to_quaternion(yaw: float) -> tuple[float, float, float, float]:
    """평면 yaw → (x, y, z, w). 2D 주행이라 roll/pitch는 0이다."""
    half = 0.5 * yaw
    return (0.0, 0.0, math.sin(half), math.cos(half))


def default_meters_per_tick(
    wheel_diameter_m: float, counts_per_encoder_rev: int, gear_ratio: float
) -> float:
    """`sensor_task.cpp`의 임시 상수에서 유도한 기본 스케일.

    MT6701은 14비트 절대각(16384 counts/rev)을 감속기 **입력축**에서 읽으므로
    바퀴 1회전당 tick = counts_per_encoder_rev × gear_ratio다.
    """
    return (math.pi * wheel_diameter_m) / (counts_per_encoder_rev * gear_ratio)

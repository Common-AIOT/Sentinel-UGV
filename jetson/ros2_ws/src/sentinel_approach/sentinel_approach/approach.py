"""사람 접근 판정 (S15P11A301-247, 명세 25.3·24.2~24.3).

`rclpy` 를 import 하지 않는다. 여기가 이 패키지의 전부이고 노드는 배선만 한다 —
`sentinel_exploration` 과 같은 구조이며, 그래야 CI 가 이 판정을 지킨다.

## 왜 bearing-only 부터인가

탐지 노드가 `position` 을 **항상 `null`** 로 낸다(`ai/detection/src/candidates.py`).
지도 좌표가 없으므로 「사람 앞 안전거리 지점을 Nav2 목표로」(25.3 의 `MEASURED` 경로)는
지금 만들 수 없다. 카메라 방위각과 그 방향의 LiDAR 거리만으로 가는 것이
`ESTIMATED`·bearing-only 경로이고, 티켓이 「Nav2 없이 동작하므로 그것부터」라고 적어 둔
이유다. `MEASURED` 가 생기면 이 모듈 밖에서 목표를 만들고 여기는 그대로 둔다.

## 제자리 회전은 하지 않는다 (2026-08-06 차체 변경)

**S15P11A301-247 본문의 「방위 정렬(제자리 회전) 후 전진」은 차동 구동 전제의 낡은
서술이다.** 전륜 서보 조향으로 바뀌면서 제자리 회전이 불가능해졌고, `vehicle_kinematics`
는 `v≈0` 에 `ω≠0` 인 명령을 **거부한다**(§34-2, 실측 2026-08-06: 「전륜 조향 차량은
제자리 회전을 할 수 없다」 경고). 그래서 접근은 **전진하면서 조향하는 호**다.

그 대가로 곡률 상한이 생긴다 — `κ = ω/v ≤ 1/R_min`. 카메라 수평 화각이 ±26° 라
방위 오차가 그 안이고, `R_min` 1.69m 면 한 번에 못 감싸는 각도가 남을 수 있다. 그때는
**호를 그리며 접근하다 다음 관측에서 다시 조향한다** — 사람이 화각 밖으로 나가면
그것은 추적 상실이고, 노드가 정지시킨다.

## 이 모듈이 하지 않는 것

- **충돌 회피.** 안전거리보다 가까워지면 멈추는 것은 `collision_monitor` 의 정지 구역과
  `safety_gate` 의 초음파 보호정지가 한다(24.3, S15P11A301-237). 여기서 다시 구현하면
  같은 판정이 두 곳에 생기고 언젠가 한쪽만 바뀐다.
- **상태 전이.** 도착·실패를 신호로 낼 뿐 `INTERACTING` 으로 보내는 것은
  `mission_manager` 다(26.1 단일 권한).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: 접근 도달로 볼 거리. 30.3 의 「안전 관측 위치」는 실측 전이라 잠정값이다.
#: `collision_monitor` 의 전방 정지 구역(범퍼 앞 0.25m = base 기준 1.05m,
#: S15P11A301-356)보다 **크게** 잡는다 — 그보다 작으면 사람 다리가 정지 구역에
#: 먼저 들어와 안전 체인이 속도를 0 으로 만들고 이 모듈은 영원히 도착을
#: 선언하지 못한다. 구역을 바꾸면 이 값을 같이 본다 (0.60 → 1.20, S15P11A301-357).
DEFAULT_STOP_DISTANCE_M = 1.20

#: 24.2 「피해자 접근」 행. 이 값은 여기와 안전 체인이 함께 지킨다.
#:
#: **0.10 → 0.15(S15P11A301-343) → 0.25 (2026-08-09, S15P11A301-342).** 종전
#: 0.15 는 속도 보고가 3.4배 부풀려진 결함(339) 위에서 고른 값이었다. 결함 수정
#: 후 재실측하니 0.15 는 펌웨어 데드밴드(150mm/s) 경계값이라 실속도가 사실상 0
#: 이고, 0.20 명령도 정지 마찰 구간이라 실속도 39%(78mm/s)다. **확실히 움직이는
#: 최저 대역이 0.25 다**(명령 대비 실속도 82%, 205mm/s — 줄자 102.5cm/5s).
#:
#: 순항(24.2 자율 탐사)은 0.30(명령=실속도 99%)이고, 접근은 사람을 향해 가므로
#: 한 단 낮은 0.25 를 쓴다 — 실속도 약 0.2m/s 로 접근 감속의 의미가 남는다.
DEFAULT_MAX_SPEED_MPS = 0.25

#: 03장 34-2 의 계산값 R_min = L/tan(δ_max) = 0.683/tan(22°). 실측(S15P11A301-299)이
#: 오면 이 기본값을 고친다.
DEFAULT_MIN_TURNING_RADIUS_M = 1.69


@dataclass(frozen=True)
class ApproachLimits:
    """접근 주행의 상한. 노드가 파라미터에서 만들어 넘긴다."""

    max_speed_mps: float = DEFAULT_MAX_SPEED_MPS
    min_turning_radius_m: float = DEFAULT_MIN_TURNING_RADIUS_M
    stop_distance_m: float = DEFAULT_STOP_DISTANCE_M

    @property
    def max_curvature(self) -> float:
        """1/R_min. 조향 차량이 낼 수 있는 곡률의 상한이다."""
        return 1.0 / self.min_turning_radius_m


@dataclass(frozen=True)
class ApproachCommand:
    """한 틱의 결정.

    `linear_mps` 가 0 이면 `angular_radps` 도 0 이다 — **그 불변식이 이 자료형의
    존재 이유다.** 조향 차량에 `v=0·ω≠0` 을 주면 `vehicle_kinematics` 가 거부하고,
    Nav2 진행 검사는 그것을 「정체」로 읽어 목표를 버린다(2026-08-06 실측).
    """

    linear_mps: float
    angular_radps: float
    arrived: bool
    reason: str

    def __post_init__(self) -> None:
        if self.linear_mps == 0.0 and self.angular_radps != 0.0:
            raise ValueError(
                '조향 차량은 제자리 회전을 못 한다 — v=0 이면 ω 도 0 이어야 한다'
            )


STOPPED = ApproachCommand(0.0, 0.0, False, 'stopped')


def bearing_from_box(
    center_x_px: float, image_width_px: float, horizontal_fov_rad: float
) -> float:
    """bbox 중심 x → 카메라 방위각(rad). 오른쪽이 음수다(ROS 오른손 좌표계).

    핀홀 근사를 쓴다. 정확히는 `atan((2u/W - 1)·tan(hfov/2))` 이고 화각이 좁을수록
    선형 근사와의 차이가 작지만, **광각 끝에서 오차가 커지는 쪽이 사람을 놓치는
    방향**이라 근사하지 않는다.
    """
    if image_width_px <= 0:
        raise ValueError('image_width_px 는 양수여야 한다')
    normalized = (2.0 * center_x_px / image_width_px) - 1.0
    return -math.atan(normalized * math.tan(horizontal_fov_rad / 2.0))


def range_at_bearing(
    ranges: list[float],
    angle_min: float,
    angle_increment: float,
    bearing_rad: float,
    *,
    window: int = 2,
    range_min: float = 0.0,
    range_max: float = math.inf,
) -> float | None:
    """그 방위 부근 LiDAR 빔들의 **중앙값**. 유효 빔이 없으면 None.

    평균이 아니라 중앙값인 이유는 사람 옆으로 빠지는 빔(뒤 벽까지 가는 값)이 섞이기
    때문이다 — 평균은 그 하나에 끌려가지만 중앙값은 버틴다.

    `None` 은 「거리를 모른다」이고 「멀다」가 아니다. 호출부는 그 둘을 섞으면 안 된다 —
    모르는 채로 전진하면 사람을 지나쳐 간다.
    """
    if not ranges or angle_increment == 0:
        return None
    index = int(round((bearing_rad - angle_min) / angle_increment))
    lo = max(0, index - window)
    hi = min(len(ranges), index + window + 1)
    if lo >= hi:
        return None
    valid = [
        r for r in ranges[lo:hi]
        if r is not None and not math.isnan(r) and range_min < r < range_max
    ]
    if not valid:
        return None
    valid.sort()
    middle = len(valid) // 2
    if len(valid) % 2 == 1:
        return valid[middle]
    return (valid[middle - 1] + valid[middle]) / 2.0


def plan_approach(
    *,
    bearing_rad: float,
    distance_m: float | None,
    limits: ApproachLimits,
    speed_limit_mps: float | None = None,
) -> ApproachCommand:
    """한 틱의 접근 명령.

    `distance_m` 가 `None` 이면 **정지한다.** 거리를 모르는 채로 전진하면 사람을
    지나치거나 들이받는다 — 그때 멈추는 것은 안전 체인의 몫이지만, 알면서 그 층에
    기대는 것은 설계가 아니다.

    `speed_limit_mps` 는 `/mission/status` 의 `speedLimit` 이다. 상태 머신이 그 상태의
    상한을 알고 있으므로 **더 낮은 쪽을 쓴다** — 두 값이 어긋나면 낮은 쪽이 안전하다.
    """
    if distance_m is None:
        return ApproachCommand(0.0, 0.0, False, 'no_range')

    if distance_m <= limits.stop_distance_m:
        return ApproachCommand(0.0, 0.0, True, 'arrived')

    speed = limits.max_speed_mps
    if speed_limit_mps is not None:
        speed = min(speed, speed_limit_mps)
    if speed <= 0.0:
        # 상한이 0 이면 움직이지 않는다. ω 도 0 이어야 한다(위 불변식).
        return ApproachCommand(0.0, 0.0, False, 'speed_limit_zero')

    # 곡률로 조향한다. 방위 오차를 **한 번에 없애려 하지 않는다** — 그러면 R_min 을
    # 넘는 곡률을 요구하고, 클램프에 걸려 어차피 같은 호가 된다. 게인은 1.0 으로
    # 두고 상한이 결정하게 한다(튜닝 표면을 늘리지 않는다).
    curvature = bearing_rad
    curvature = max(-limits.max_curvature, min(limits.max_curvature, curvature))
    return ApproachCommand(speed, speed * curvature, False, 'approaching')

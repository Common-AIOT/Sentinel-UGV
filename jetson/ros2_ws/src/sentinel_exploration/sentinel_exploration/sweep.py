"""관측 스윕 계획 (S15P11A301-172 설계 v2 — 스텝-드웰).

목표에 도착하면 제자리 회전으로 카메라를 훑는다. **연속 회전이 아니라
스텝-드웰**이다. encounter 확정 기준이 "confidence ≥ 0.60 + 약 1초 안정
관측(ByteTrack)"이라(25.1), 연속으로 돌면 사람이 화각을 지나가는 동안 1초가
안 채워질 수 있다. 40° 돌고 → 멈춰서 1.5초(10.8FPS 에서 약 16프레임) 보고 →
다음 스텝이 기본이며, 드웰을 마친 부채꼴만 커버리지에 마킹한다(노드 몫).

여기는 **어느 방위를 돌 것인가**만 계산한다. 실제 회전 명령·드웰 타이머·중단
게이트(movement_allowed 를 스텝마다 확인)는 노드가 한다.
"""

from __future__ import annotations

import math


def _wrap(angle: float) -> float:
    """(-π, π] 로 접는다."""
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def sector_index(bearing: float, n_sectors: int) -> int:
    """절대 방위(라디안) → 부채꼴 번호. 부채꼴 0 은 -π 에서 시작한다."""
    width = 2.0 * math.pi / n_sectors
    index = int((_wrap(bearing) + math.pi) / width)
    return min(index, n_sectors - 1)  # bearing == π 정확히일 때의 경계


def sector_center(index: int, n_sectors: int) -> float:
    width = 2.0 * math.pi / n_sectors
    return _wrap(-math.pi + (index + 0.5) * width)


def needed_sectors(
    x: float,
    y: float,
    unseen: list[tuple[float, float]],
    *,
    n_sectors: int = 9,
    range_m: float = 5.0,
) -> list[bool]:
    """현재 위치에서 어느 부채꼴에 미관측 자유 공간이 남아 있는가.

    입력 `unseen` 은 `CameraCoverage.unseen_free()` 의 결과다. 시야 차단(벽)은
    여기서 다시 따지지 않는다 — 벽 뒤 미관측 지점 때문에 그 방위를 돌면 드웰
    한 번을 낭비할 뿐이고, 그 지점은 나중에 관측 목표(소스 B)로 소비된다.
    """
    needed = [False] * n_sectors
    range_sq = range_m * range_m
    for px, py in unseen:
        dx, dy = px - x, py - y
        if dx * dx + dy * dy > range_sq:
            continue
        needed[sector_index(math.atan2(dy, dx), n_sectors)] = True
    return needed


def plan_sweep(current_yaw: float, needed: list[bool], *, n_sectors: int | None = None) -> list[float]:
    """돌아야 할 부채꼴 중심 yaw 목록. 회전량이 적은 순서로 탐욕 정렬한다.

    현재 정면 부채꼴은 계획에서 뺀다 — 도착 자세의 yaw 를 미관측 방향으로
    잡으므로(선택기 몫) 정면은 도착 드웰이 이미 처리한다.
    """
    if n_sectors is None:
        n_sectors = len(needed)
    facing = sector_index(current_yaw, n_sectors)
    targets = [
        sector_center(i, n_sectors)
        for i, need in enumerate(needed)
        if need and i != facing
    ]

    ordered: list[float] = []
    yaw = current_yaw
    remaining = list(targets)
    while remaining:
        nearest = min(remaining, key=lambda t: abs(_wrap(t - yaw)))
        ordered.append(nearest)
        remaining.remove(nearest)
        yaw = nearest
    return ordered


def sweep_duration_s(
    n_steps: int,
    *,
    step_rad: float = math.radians(40.0),
    rotate_speed_radps: float = 0.5,
    dwell_s: float = 1.5,
) -> float:
    """스윕 시간 추정. 7분 예산 안에서 목표 수를 가늠할 때 쓴다."""
    return n_steps * (step_rad / rotate_speed_radps + dwell_s)

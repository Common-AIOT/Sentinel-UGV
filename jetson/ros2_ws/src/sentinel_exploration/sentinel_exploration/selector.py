"""목표 점수화와 약속(commitment) 정책 (S15P11A301-172, 명세 23.3).

## 점수

    score = f(방위) · [w_map · 지도 정보량(m²) + w_camera · 카메라 미관측(m²)]
          − w_dist · (경로 길이 + R·|방위각|)(m) − w_visit · 방문 감점(회)

방위각 두 항은 전방 편향이다(S15P11A301-357·360) — 후방 후보는 그쪽을 향해
도는 호 길이만큼 멀게 치고, 이득도 f(전방 1.0 → 정후방 0.15)로 감쇠한다.
score() 와 REAR_GAIN_FLOOR 의 주석에 근거가 있다.

항을 전부 물리 단위(m²·m·회)로 두는 것이 의도다. 가중치가 무차원이 되어 "거리
1m 를 정보 1m² 와 몇 대 몇으로 칠 것인가"를 그대로 말한다. 정규화 없이 셀 수와
미터를 섞으면 해상도를 바꿀 때마다 가중치를 다시 찾아야 한다.

경로 길이는 **Nav2 플래너가 준 값**을 쓴다. 직선거리는 벽 뒤 후보를 가까워
보이게 만든다. 플래너가 아직 없으면(#235 전) 직선거리 × 1.5 로 대체한다 —
낙관을 줄이는 방향의 보정이다.

## 약속

지도가 2초마다 갱신되어 후보 순위가 계속 바뀐다. 매번 최고점을 다시 고르면
로봇이 두 frontier 사이를 왕복한다 — frontier 탐사의 가장 고전적인 실패다.
그래서 한번 고른 목표는 다음 세 경우에만 놓는다.

1. 도달했거나 Nav2 가 불능 판정
2. 군집이 지도 갱신으로 소멸 (`frontier.cluster_alive` — 진동이 아니라 진전)
3. 새 후보가 **충분히 오래된** 현재 목표를 **충분한 차이**로 이길 때
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .coverage import CameraCoverage
from .grid import GridInfo, free_mask, unknown_mask, world_to_cell


@dataclass(frozen=True)
class Weights:
    w_map: float = 1.0
    w_camera: float = 1.5  # 수색이 컨셉이다 — 사람을 볼 기회를 지도보다 무겁게 친다
    w_dist: float = 0.5
    w_visit: float = 2.0


@dataclass
class Candidate:
    x: float
    y: float
    kind: str  # 'frontier' | 'observation'
    map_gain_m2: float = 0.0
    camera_gain_m2: float = 0.0
    path_len_m: float | None = None
    payload: object = None  # frontier 면 FrontierCluster — 소멸 판정에 쓴다


def compute_gains(
    grid: np.ndarray,
    info: GridInfo,
    coverage: CameraCoverage,
    x: float,
    y: float,
    *,
    radius_m: float = 3.0,
) -> tuple[float, float]:
    """후보 주변의 (지도 정보량, 카메라 미관측) 면적(m²).

    반경 창 안의 셀 수 기반이다. 시야 레이캐스트 기반이 더 정확하지만 후보마다
    수백 레이를 쏘게 되어, 1Hz 선택 주기에 후보 수십 개면 예산을 넘는다. 창
    방식의 편향(벽 너머 미지도 센다)은 순위에 고르게 끼므로 선택을 크게
    비틀지 않는다.
    """
    center = world_to_cell(info, x, y)
    if center is None:
        return 0.0, 0.0
    row, col = center
    radius_cells = int(radius_m / info.resolution)

    row_lo = max(0, row - radius_cells)
    row_hi = min(info.height, row + radius_cells + 1)
    col_lo = max(0, col - radius_cells)
    col_hi = min(info.width, col + radius_cells + 1)
    window = grid[row_lo:row_hi, col_lo:col_hi]

    cell_area = info.resolution * info.resolution
    map_gain = float(unknown_mask(window).sum()) * cell_area

    # 카메라 이득은 장부 해상도(0.25m)로 성기게 센다.
    camera_gain = 0.0
    step = coverage.cell_m
    steps = int(radius_m / step)
    free = free_mask(grid)
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            px, py = x + i * step, y + j * step
            cell = world_to_cell(info, px, py)
            if cell is None or not free[cell[0], cell[1]]:
                continue
            if not coverage.is_seen(px, py):
                camera_gain += step * step
    return map_gain, camera_gain


def visit_penalty(history: list[tuple[float, float]], x: float, y: float, *, radius_m: float = 2.0) -> int:
    """이전 목표 지점 이력에 대한 감점(반경 내 방문 횟수)."""
    radius_sq = radius_m * radius_m
    return sum(1 for hx, hy in history if (hx - x) ** 2 + (hy - y) ** 2 <= radius_sq)


#: 전방 편향의 회전 반경 (S15P11A301-357). 방위각을 「그쪽을 향하는 데 드는
#: 호 길이」로 환산할 때 쓴다. 실측 R_min 이 좌 1.37m·우 1.76m 라 안전측으로
#: 잡았다 — Smac 전환(S15P11A301-358)으로 후진 계획이 생기면 낮출 수 있다.
TURN_RADIUS_M = 1.8

#: 정후방 후보의 이득 감쇠 하한 (S15P11A301-360, 강화 S15P11A301-363).
#: 357 의 거리 가산만으로는 부족했다 — 실기동에서 정후방 페널티가
#: w_dist 0.5 × 5.65m ≈ **3점**인데 카메라 이득은 수십 점(반경 3m 창 최대
#: 28m² × w_camera 1.5 = 42점)이라 완전히 파묻혔고, 목표 6개가 전부 후방
#: (방위 ±90~178°)으로 뽑혀 로봇이 좌우로 헤맸다. 그래서 이득 자체를 방위로
#: 감쇠한다 — 곱셈이라 이득이 아무리 커도 비율이 유지된다.
#:
#: **0.3 → 0.15 (2026-08-09 세 번째 실기동).** 0.3 으로도 후방 목표가 계속
#: 뽑혔고, 그 목표들이 status=6 으로 실패하기까지 **각각 65초·70초**를 잡아먹었다
#: (그 사이 Nav2 는 local_costmap 클리어 복구만 반복 — 「조금 가다 멈춤」의 정체).
#: 실패한 목표가 전부 후방이었다는 것이 이 값을 더 내리는 근거다.
#:
#: 대안이었던 「failure_tolerance 를 낮춰 빨리 포기시키기」를 택하지 않은 이유:
#: 그것은 **전방 목표의 일시적 장애까지** 성급히 버려 탐사 진행을 느리게 한다.
#: 못 가는 목표를 빨리 포기하는 것보다 애초에 안 고르는 것이 근본이다.
#: 감쇠 곡선: 전방 1.0, 측면 0.575, 정후방 0.15.
REAR_GAIN_FLOOR = 0.15


def score(
    candidate: Candidate,
    weights: Weights,
    *,
    from_x: float,
    from_y: float,
    from_yaw: float | None = None,
    history: list[tuple[float, float]],
) -> float:
    if candidate.path_len_m is not None:
        distance = candidate.path_len_m
    else:
        # 플래너 없음(#235 전) — 직선거리에 1.5 배. 벽 우회를 뭉뚱그린 보정이며
        # 낙관(가깝다고 착각)을 줄이는 방향이다.
        distance = math.hypot(candidate.x - from_x, candidate.y - from_y) * 1.5
    gain_factor = 1.0
    if from_yaw is not None:
        # 전방 편향 (S15P11A301-357, 강화 S15P11A301-360). NavFn 은 회전반경을
        # 모르고 RPP 는 곡률 상한이 없어, 후방 목표가 뽑히면 조향이 상한에 붙은
        # 채 수렴하지 못한다 (첫 실기동: steering_clamp_count 192, 전진 0).
        #
        # 두 겹으로 건다:
        # 1) 회전 호 비용 — 방위각 |Δθ| 를 그쪽을 향해 도는 호 길이 R·|Δθ| 로
        #    환산해 거리에 가산 (물리적으로 실재하는 비용).
        # 2) 이득 감쇠 — 두 번째 실기동에서 1)만으로는 이득(수십 점)에 파묻히는
        #    것이 확인됐다(REAR_GAIN_FLOOR 주석). 전방 1.0 → 정후방 0.15 로
        #    이득 자체를 깎는다. Smac(358)이 후진을 계획해도 후방 목표가
        #    느린 것은 여전하므로(후진 0.3m/s + 방향전환 데드타임) 유지한다.
        bearing = math.atan2(candidate.y - from_y, candidate.x - from_x) - from_yaw
        bearing = math.atan2(math.sin(bearing), math.cos(bearing))
        distance += TURN_RADIUS_M * abs(bearing)
        gain_factor = (REAR_GAIN_FLOOR
                       + (1.0 - REAR_GAIN_FLOOR) * (1.0 + math.cos(bearing)) / 2.0)
    return (
        gain_factor * (
            weights.w_map * candidate.map_gain_m2
            + weights.w_camera * candidate.camera_gain_m2
        )
        - weights.w_dist * distance
        - weights.w_visit * visit_penalty(history, candidate.x, candidate.y)
    )


@dataclass
class Commitment:
    """현재 약속한 목표. 진동 방지의 전부가 이 작은 클래스다."""

    candidate: Candidate
    committed_score: float
    committed_at: float  # 단조 시계(초). 노드가 주입한다 — 여기서 시계를 잡으면 시험이 느려진다

    margin: float = 1.25
    min_age_s: float = 5.0

    def should_replace(self, new_score: float, now: float) -> bool:
        """새 후보가 약속을 깰 자격이 있는가.

        나이 조건이 먼저다. 갓 고른 목표를 지도 한 번 갱신됐다고 갈아치우면
        margin 이 있어도 진동한다 — 갱신 직후에는 점수 자체가 요동치기 때문이다.
        """
        if now - self.committed_at < self.min_age_s:
            return False
        if self.committed_score <= 0:
            # 약속 시점 점수가 0 이하면 비교 배수가 무의미하다. 절대 비교로.
            return new_score > 0
        return new_score > self.committed_score * self.margin

"""Frontier 추출 (S15P11A301-172, 명세 23.3).

frontier = 미지(-1) 셀과 4-이웃으로 맞닿은 자유 셀. 그것을 8-연결로 군집화하고,
아래 세 필터를 통과한 군집의 대표점을 후보로 낸다.

1. **크기** — `min_cells` 미만 군집은 버린다. 라이다 노이즈가 만든 한두 셀짜리
   가짜 경계다.
2. **격자 테두리** — 테두리에 닿은 군집은 버린다. slam_toolbox 격자는 로봇이
   움직일수록 커지고 바깥 테두리는 항상 미지라서, 테두리 frontier 를 좇으면
   로봇이 지도를 끝없이 밖으로 넓히기만 한다.
3. **반경 상한** — home 에서 `max_radius_m` 를 넘는 후보는 버린다. 시연장
   경계다. 테두리 필터가 놓치는 방향(지도가 이미 커진 쪽)을 이것이 막는다.

scipy 를 쓰지 않는 것이 의도다. CI 이미지(alpine·slim)에 부담을 줄이고, frontier
셀은 전체 격자의 극히 일부라 BFS 가 충분히 싸다(실측 351×372 지도에서 수 ms).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .grid import GridInfo, cell_to_world, free_mask, unknown_mask

# 8-연결 이웃. 군집화 전용이다 — 미지 인접 판정은 4-이웃을 쓴다. 대각선만 닿은
# 미지는 라이다 광선이 지나갔을 가능성이 높아 경계로 보지 않는다(WFD 관례).
_NEIGHBORS_8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


@dataclass(frozen=True)
class FrontierCluster:
    """군집 하나. `rep` 는 항상 군집 소속 셀이므로 자유 공간이 보장된다."""

    cells: tuple[tuple[int, int], ...]
    rep_x: float
    rep_y: float

    @property
    def size(self) -> int:
        return len(self.cells)


def frontier_mask(grid: np.ndarray) -> np.ndarray:
    """자유이면서 미지와 4-이웃으로 맞닿은 셀.

    numpy 시프트 비교로 만든다. 파이썬 이중 루프는 130k 셀에서 수백 ms 라
    선택 주기(1Hz)에 얹기엔 무겁다.
    """
    free = free_mask(grid)
    unknown = unknown_mask(grid)

    adjacent = np.zeros_like(unknown)
    adjacent[:-1, :] |= unknown[1:, :]
    adjacent[1:, :] |= unknown[:-1, :]
    adjacent[:, :-1] |= unknown[:, 1:]
    adjacent[:, 1:] |= unknown[:, :-1]
    return free & adjacent


def _retreat_into_free(
    grid: np.ndarray, row: int, col: int, steps: int
) -> tuple[int, int]:
    """frontier 셀에서 자유 공간 안쪽으로 `steps` 칸 물러난 셀 (S15P11A301-366).

    방향은 5×5 창의 자유 셀 무게중심으로 정한다 — 미지 쪽은 자유 셀이 없으므로
    자연스럽게 반대편(이미 탐사한 열린 공간)을 가리킨다. 후퇴 경로 위의 셀을
    하나씩 보며 **자유인 마지막 지점**을 고른다. 벽에 막히면 거기서 멈추므로
    후퇴가 상황을 나쁘게 만들지 않는다.
    """
    if steps <= 0:
        return row, col
    free = free_mask(grid)
    height, width = grid.shape

    lo_r, hi_r = max(0, row - 2), min(height, row + 3)
    lo_c, hi_c = max(0, col - 2), min(width, col + 3)
    window = free[lo_r:hi_r, lo_c:hi_c]
    rows, cols = np.nonzero(window)
    if len(rows) == 0:
        return row, col
    dir_r = float((rows + lo_r).mean()) - row
    dir_c = float((cols + lo_c).mean()) - col
    norm = (dir_r ** 2 + dir_c ** 2) ** 0.5
    if norm < 1e-6:
        return row, col
    dir_r, dir_c = dir_r / norm, dir_c / norm

    best = (row, col)
    for step in range(1, steps + 1):
        r = int(round(row + dir_r * step))
        c = int(round(col + dir_c * step))
        if not (0 <= r < height and 0 <= c < width) or not free[r, c]:
            break
        best = (r, c)
    return best


def extract_frontiers(
    grid: np.ndarray,
    info: GridInfo,
    *,
    min_cells: int = 6,
    home_x: float = 0.0,
    home_y: float = 0.0,
    max_radius_m: float = 12.0,
    retreat_m: float = 0.6,
) -> list[FrontierCluster]:
    """frontier 군집을 뽑는다. 반환 순서는 결정적이다(첫 셀의 행 우선).

    `min_cells` 기본 6 은 0.3m / 0.05m/셀이다. 해상도가 바뀌면 호출자가
    다시 계산해야 하므로 셀 수로 받는다 — 미터로 받아 내부에서 나누면
    "0.3m 군집"이 해상도에 따라 다른 것을 뜻하게 된다.
    """
    mask = frontier_mask(grid)
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    clusters: list[FrontierCluster] = []
    max_radius_sq = max_radius_m * max_radius_m

    rows, cols = np.nonzero(mask)
    for start_row, start_col in zip(rows.tolist(), cols.tolist()):
        if visited[start_row, start_col]:
            continue

        # BFS 로 8-연결 성분을 모은다.
        cells: list[tuple[int, int]] = []
        touches_border = False
        queue = deque([(start_row, start_col)])
        visited[start_row, start_col] = True
        while queue:
            row, col = queue.popleft()
            cells.append((row, col))
            if row == 0 or col == 0 or row == height - 1 or col == width - 1:
                touches_border = True
            for d_row, d_col in _NEIGHBORS_8:
                n_row, n_col = row + d_row, col + d_col
                if (
                    0 <= n_row < height
                    and 0 <= n_col < width
                    and mask[n_row, n_col]
                    and not visited[n_row, n_col]
                ):
                    visited[n_row, n_col] = True
                    queue.append((n_row, n_col))

        if touches_border or len(cells) < min_cells:
            continue

        # 대표점: centroid 에 가장 가까운 **군집 소속** 셀. centroid 자체를 쓰면
        # ㄱ자 군집에서 벽 안이나 미지에 찍힌다 — float64 로는 유효한 좌표라서
        # 예외 없이 통과하고, Nav2 가 도달 불가 목표로 계속 실패하는 형태로만
        # 드러난다.
        cell_array = np.asarray(cells)
        centroid = cell_array.mean(axis=0)
        nearest = cell_array[np.argmin(((cell_array - centroid) ** 2).sum(axis=1))]
        anchor_row, anchor_col = int(nearest[0]), int(nearest[1])

        # **자유 공간 쪽으로 물러난다** (S15P11A301-366).
        #
        # frontier 셀은 「자유이면서 미지에 맞닿은」 셀이라 정의상 미지 경계에
        # 붙어 있다. 그 자리를 그대로 목표로 주면 Nav2 가 거부한다 — global
        # costmap 에서 그 근방이 미지(-1)이거나 inflation_radius(0.50) 팽창에
        # 걸린 고비용이기 때문이다. 2026-08-09 실측: frontier 대표점 3개가 SLAM
        # 지도에서는 전부 자유였는데 costmap 값은 -1 / 66 / 66 이었고,
        # ComputePathToPose 가 셋 다 ABORTED 였다. 증상은 「탐사가 도는데 로봇이
        # 한 발짝도 안 움직인다」 하나뿐이다.
        #
        # 그래서 대표점을 군집에서 자유 공간 안쪽으로 retreat_m 만큼 민다.
        # 방향은 「군집 중심 → 자유 이웃이 많은 쪽」이고, 후퇴한 자리가 자유가
        # 아니면 원래 자리를 쓴다(후퇴가 상황을 나쁘게 만들지는 않게).
        rep_row, rep_col = _retreat_into_free(
            grid, anchor_row, anchor_col, int(round(retreat_m / info.resolution))
        )
        rep_x, rep_y = cell_to_world(info, rep_row, rep_col)

        if (rep_x - home_x) ** 2 + (rep_y - home_y) ** 2 > max_radius_sq:
            continue

        clusters.append(
            FrontierCluster(cells=tuple(map(tuple, cells)), rep_x=rep_x, rep_y=rep_y)
        )

    return clusters


def cluster_alive(grid: np.ndarray, cluster: FrontierCluster, *, survivors: int = 3) -> bool:
    """약속한 목표의 군집이 아직 frontier 인가.

    지도 갱신으로 군집 셀 대부분이 이미 관측됐으면(자유/점유로 확정) 그 목표는
    소멸한 것이다 — 선택기가 진동 방지 약속을 깨고 즉시 재선택해야 하는 유일한
    경우다. 전부 소멸을 요구하지 않고 `survivors` 미만이면 죽은 것으로 본다.
    경계가 몇 셀 남는 것은 흔하고, 그것 때문에 다 본 방으로 계속 가면 안 된다.
    """
    mask = frontier_mask(grid)
    height, width = mask.shape
    alive = 0
    for row, col in cluster.cells:
        if 0 <= row < height and 0 <= col < width and mask[row, col]:
            alive += 1
            if alive >= survivors:
                return True
    return False

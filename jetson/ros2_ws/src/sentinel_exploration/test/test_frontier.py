"""Frontier 추출 시험 (S15P11A301-172).

픽스처는 **돌고 있는 slam_toolbox 에서 캡처한 실제 지도**(351×372, 0.05m/셀)다.
손으로 만든 격자로는 실지도의 너덜너덜한 경계(라이다 노이즈·부분 관측)가
재현되지 않아서, 필터가 실제로 무엇을 거르는지 시험이 말해주지 못한다.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from sentinel_exploration.frontier import (
    FrontierCluster,
    cluster_alive,
    extract_frontiers,
    frontier_mask,
)
from sentinel_exploration.grid import GridInfo, free_mask, world_to_cell

FIXTURE = pathlib.Path(__file__).parent / 'fixtures' / 'map_351x372.npz'


@pytest.fixture(scope='module')
def real_map() -> tuple[np.ndarray, GridInfo]:
    data = np.load(FIXTURE)
    grid = data['grid']
    info = GridInfo(
        resolution=float(data['resolution']),
        origin_x=float(data['origin_x']),
        origin_y=float(data['origin_y']),
        width=grid.shape[1],
        height=grid.shape[0],
    )
    return grid, info


def test_실지도에서_군집이_나온다(real_map):
    grid, info = real_map
    clusters = extract_frontiers(grid, info)
    # 미지 66k·자유 57k 셀이 섞인 지도다. 경계가 없다면 추출이 죽은 것이다.
    assert len(clusters) >= 1


def test_대표점은_항상_자유_셀이다(real_map):
    # centroid 를 그대로 쓰면 ㄱ자 군집에서 벽·미지에 찍힌다. 대표점이 자유가
    # 아니면 Nav2 가 도달 불가 목표로 계속 실패하고, 그 실패는 화면에 안 보인다.
    grid, info = real_map
    free = free_mask(grid)
    for cluster in extract_frontiers(grid, info):
        cell = world_to_cell(info, cluster.rep_x, cluster.rep_y)
        assert cell is not None
        assert free[cell[0], cell[1]]


def test_대표점은_군집_소속_셀이다(real_map):
    grid, info = real_map
    for cluster in extract_frontiers(grid, info):
        cell = world_to_cell(info, cluster.rep_x, cluster.rep_y)
        assert cell in cluster.cells


def test_최소_크기_미만은_버린다(real_map):
    grid, info = real_map
    for cluster in extract_frontiers(grid, info, min_cells=6):
        assert cluster.size >= 6


def test_반경_상한이_후보를_거른다(real_map):
    grid, info = real_map
    wide = extract_frontiers(grid, info, max_radius_m=100.0)
    narrow = extract_frontiers(grid, info, max_radius_m=2.0)
    assert len(narrow) <= len(wide)
    for cluster in narrow:
        assert cluster.rep_x**2 + cluster.rep_y**2 <= 2.0**2 + 1e-6


def test_테두리_군집은_버린다():
    # slam_toolbox 격자의 바깥 테두리는 항상 미지라서, 테두리 frontier 를 좇으면
    # 로봇이 지도를 끝없이 밖으로 넓히기만 한다.
    grid = np.zeros((10, 10), dtype=np.int8)          # 전부 자유
    grid[0, :] = -1                                    # 위쪽 테두리가 미지
    info = GridInfo(resolution=0.05, origin_x=0.0, origin_y=0.0, width=10, height=10)
    # 미지에 맞닿은 자유 셀(행 1)이 frontier 지만 그 군집은 테두리(열 0·9)에 닿는다.
    assert extract_frontiers(grid, info, min_cells=1) == []


def test_frontier는_자유와_미지의_경계다():
    grid = np.full((5, 5), -1, dtype=np.int8)
    grid[2, 2] = 0          # 자유 섬 하나
    grid[2, 3] = 100        # 벽
    mask = frontier_mask(grid)
    assert mask[2, 2]           # 미지와 4-이웃 — frontier
    assert not mask[2, 3]       # 벽은 frontier 가 아니다
    assert not mask[0, 0]       # 미지 자신도 아니다


def test_벽에만_닿은_자유는_frontier가_아니다():
    grid = np.zeros((3, 3), dtype=np.int8)
    grid[0, :] = 100
    grid[2, :] = 100
    grid[:, 0] = 100
    grid[:, 2] = 100
    # 가운데 자유 셀은 사방이 벽 — 미지가 없으니 frontier 없음.
    assert not frontier_mask(grid).any()


def test_군집_소멸_판정(real_map):
    grid, info = real_map
    clusters = extract_frontiers(grid, info)
    target = clusters[0]
    assert cluster_alive(grid, target)

    # 군집 자리를 전부 관측(자유)으로 바꾸면 소멸 — 미지 이웃이 사라진다.
    observed = grid.copy()
    for row, col in target.cells:
        row_lo, row_hi = max(0, row - 1), min(observed.shape[0], row + 2)
        col_lo, col_hi = max(0, col - 1), min(observed.shape[1], col + 2)
        observed[row_lo:row_hi, col_lo:col_hi] = 0
    assert not cluster_alive(observed, target)


def test_소멸_판정은_생존자_수를_센다():
    cluster = FrontierCluster(cells=((1, 1), (1, 2), (1, 3), (1, 4)), rep_x=0.0, rep_y=0.0)
    grid = np.full((6, 6), -1, dtype=np.int8)
    grid[1, 1:5] = 0    # 네 셀 전부 아직 frontier (아래가 미지)
    assert cluster_alive(grid, cluster, survivors=3)
    grid[0:3, 1:4] = 0  # 셀 1~3 주변 관측 → 생존자는 (1,4) 하나
    assert not cluster_alive(grid, cluster, survivors=3)


def test_미지가_어느_방향에_있든_frontier다():
    # 4-이웃 판정은 numpy 시프트 4개로 만든다. 한 방향이 빠져도 나머지가
    # 대부분을 덮어서 실지도 시험은 통과한다 — 방향별로 못박아야 잡힌다.
    info = GridInfo(resolution=0.05, origin_x=0.0, origin_y=0.0, width=5, height=5)
    for d_row, d_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        grid = np.zeros((5, 5), dtype=np.int8)
        grid[2 + d_row, 2 + d_col] = -1
        assert frontier_mask(grid)[2, 2], f'미지 방향 {(d_row, d_col)} 을 놓쳤다'

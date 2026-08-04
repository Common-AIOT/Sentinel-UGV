"""카메라 커버리지 장부 시험 (S15P11A301-172).

여기서 틀리면 두 방향으로 죽는다. 과대(벽 너머를 봤다고 기록)면 **사람을 안 보고
지나가고**, 과소면 같은 자리를 무한히 다시 돈다. 전자가 훨씬 나쁘므로 모든
모호함은 과소 방향으로 접는다 — 그 접힘이 실제로 그 방향인지가 이 시험들이다.
"""

from __future__ import annotations

import math

import numpy as np

from sentinel_exploration.coverage import CameraCoverage, observation_candidates
from sentinel_exploration.grid import GridInfo


def open_room(size: int = 40, resolution: float = 0.05) -> tuple[np.ndarray, GridInfo]:
    grid = np.zeros((size, size), dtype=np.int8)
    info = GridInfo(resolution=resolution, origin_x=0.0, origin_y=0.0, width=size, height=size)
    return grid, info


def test_전방만_기록한다():
    grid, info = open_room()
    coverage = CameraCoverage()
    # (1,1) 에서 +x 방향, HFOV 52°
    coverage.mark_visible(grid, info, 1.0, 1.0, 0.0, hfov_rad=math.radians(52), range_m=0.8)
    assert coverage.is_seen(1.5, 1.0)       # 정면
    assert not coverage.is_seen(0.3, 1.0)   # 뒤
    assert not coverage.is_seen(1.0, 1.7)   # 좌측 90° — 화각 밖


def test_벽이_시야를_막는다():
    grid, info = open_room()
    grid[:, 24] = 100  # x=1.2m 에 세로 벽
    coverage = CameraCoverage()
    coverage.mark_visible(grid, info, 0.5, 1.0, 0.0, hfov_rad=math.radians(52), range_m=1.5)
    assert coverage.is_seen(1.0, 1.0)       # 벽 앞
    # 벽 너머를 봤다고 기록하면 그 구석의 사람을 영영 찾으러 가지 않는다.
    assert not coverage.is_seen(1.5, 1.0)


def test_미지가_시야를_막는다():
    grid, info = open_room()
    grid[:, 24:] = -1
    coverage = CameraCoverage()
    coverage.mark_visible(grid, info, 0.5, 1.0, 0.0, hfov_rad=math.radians(52), range_m=1.5)
    assert not coverage.is_seen(1.5, 1.0)   # 미지 너머 — 트였는지 알 수 없다


def test_유효거리_밖은_기록하지_않는다():
    grid, info = open_room(80)
    coverage = CameraCoverage()
    coverage.mark_visible(grid, info, 0.5, 1.0, 0.0, hfov_rad=math.radians(52), range_m=1.0)
    # 3.9m 지점은 자유이고 시야도 트였지만 탐지 유효거리(1m) 밖 — 그 거리에선
    # 사람이 화면에 너무 작아 탐지를 신뢰할 수 없다.
    assert not coverage.is_seen(3.9, 1.0)


def test_장부는_지도_origin_이동에_불변이다():
    # 루프 클로저·지도 성장의 핵심 성질. 장부 키가 격자 인덱스였다면 origin 이
    # 움직일 때 과거 기록 전체가 다른 자리를 가리키게 된다.
    grid, info = open_room()
    coverage = CameraCoverage()
    coverage.mark_visible(grid, info, 1.0, 1.0, 0.0, hfov_rad=math.radians(52), range_m=0.8)
    assert coverage.is_seen(1.5, 1.0)

    grown = GridInfo(resolution=0.05, origin_x=-2.0, origin_y=-3.0, width=140, height=140)
    grown_grid = np.zeros((140, 140), dtype=np.int8)
    unseen = coverage.unseen_free(grown_grid, grown)
    # world (1.5, 1.0) 부근은 새 격자에서도 이미 본 곳이어야 한다.
    assert all(abs(x - 1.5) > 0.13 or abs(y - 1.0) > 0.13 for x, y in unseen)
    assert coverage.is_seen(1.5, 1.0)


def test_마킹이_미관측_면적을_줄인다():
    grid, info = open_room()
    coverage = CameraCoverage()
    before = coverage.unseen_free_area_m2(grid, info)
    coverage.mark_visible(grid, info, 1.0, 1.0, 0.0, hfov_rad=math.radians(120), range_m=2.0)
    after = coverage.unseen_free_area_m2(grid, info)
    assert after < before


def test_encounter_주변_일괄_마킹():
    grid, info = open_room()
    coverage = CameraCoverage()
    coverage.mark_area(1.0, 1.0, 0.6)
    assert coverage.is_seen(1.0, 1.0)
    assert coverage.is_seen(1.4, 1.0)
    assert not coverage.is_seen(1.9, 1.9)


def test_관측_후보는_군집의_중심이다():
    # 한 덩어리 → 후보 1개, 서로 먼 두 덩어리 → 2개.
    blob_a = [(0.1 * i, 0.0) for i in range(5)]
    blob_b = [(5.0 + 0.1 * i, 5.0) for i in range(5)]
    candidates = observation_candidates(blob_a + blob_b, min_area_m2=0.1)
    assert len(candidates) == 2
    xs = sorted(c[0] for c in candidates)
    assert abs(xs[0] - 0.2) < 0.15
    assert abs(xs[1] - 5.2) < 0.15


def test_작은_잔여물은_후보가_아니다():
    # 종료 문턱 아래의 부스러기(문 틈 한 칸)로 로봇을 보내지 않는다.
    candidates = observation_candidates([(0.0, 0.0)], min_area_m2=0.5)
    assert candidates == []


def test_음수_좌표에서_장부_셀이_뭉개지지_않는다():
    # 실지도의 origin 은 (-9.3, -10.0) — world 좌표 대부분이 음수다. 키를
    # int()(0 방향 절단)로 만들면 -0.24~+0.24 가 전부 같은 셀이 되어, 원점
    # 부근에서 안 본 곳을 봤다고 기록한다. floor 여야 한다.
    coverage = CameraCoverage()
    coverage.mark_area(-0.05, -0.05, 0.0)
    assert coverage.is_seen(-0.05, -0.05)
    assert not coverage.is_seen(0.05, 0.05)   # 경계 반대편 — 다른 셀이다


def test_발밑이_점유여도_전방은_본다():
    # 라이다가 차체 일부를 벽으로 찍는 일이 실제로 있다(실기동 스모크에서 발밑
    # 값 100). 0m 부터 검사하면 모든 레이가 첫 셀에서 끊겨 커버리지가 영원히
    # 0이고, 그 실패는 "탐사가 끝나지 않는다"로만 보인다.
    grid, info = open_room()
    cell = int(1.0 / info.resolution)
    grid[cell - 1 : cell + 2, cell - 1 : cell + 2] = 100   # 로봇 자리만 점유
    coverage = CameraCoverage()
    coverage.mark_visible(grid, info, 1.0, 1.0, 0.0, hfov_rad=math.radians(52), range_m=1.0)
    assert coverage.is_seen(1.6, 1.0)


def test_min_range_안쪽은_기록하지_않는다():
    grid, info = open_room()
    coverage = CameraCoverage()
    coverage.mark_visible(
        grid, info, 1.0, 1.0, 0.0, hfov_rad=math.radians(52), range_m=1.0, min_range_m=0.3
    )
    # 발밑(0.05m 앞)은 건너뛴 구간 — 기록되지 않는다.
    assert not coverage.is_seen(1.05, 1.0)

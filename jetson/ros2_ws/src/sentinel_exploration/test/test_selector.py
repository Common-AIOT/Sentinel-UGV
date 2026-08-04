"""점수화·약속 정책 시험 (S15P11A301-172).

약속 정책이 이 패키지에서 가장 시험할 값어치가 있는 부분이다 — 없으면 로봇이
두 frontier 사이를 왕복하는데, 그 실패는 실차에서만 보이고 로그에는 "목표를
계속 선택 중"으로만 남는다. 여기서 규칙을 못박는다.
"""

from __future__ import annotations

import numpy as np

from sentinel_exploration.coverage import CameraCoverage
from sentinel_exploration.grid import GridInfo
from sentinel_exploration.selector import (
    Candidate,
    Commitment,
    Weights,
    compute_gains,
    score,
    visit_penalty,
)


def _candidate(**kwargs) -> Candidate:
    base = dict(x=1.0, y=0.0, kind='frontier', map_gain_m2=1.0, camera_gain_m2=0.0)
    base.update(kwargs)
    return Candidate(**base)


# ── 약속 ────────────────────────────────────────────────────────────────────

def test_어린_약속은_큰_점수로도_못_깬다():
    # 지도 갱신 직후에는 점수 자체가 요동친다. 나이 조건이 없으면 margin 이
    # 있어도 진동한다.
    commitment = Commitment(_candidate(), committed_score=10.0, committed_at=100.0)
    assert not commitment.should_replace(1000.0, now=104.9)


def test_오래된_약속도_margin_미만이면_못_깬다():
    commitment = Commitment(_candidate(), committed_score=10.0, committed_at=100.0)
    assert not commitment.should_replace(12.4, now=110.0)   # 124% < 125%


def test_오래되고_margin_초과면_깬다():
    commitment = Commitment(_candidate(), committed_score=10.0, committed_at=100.0)
    assert commitment.should_replace(12.6, now=110.0)       # 126% > 125%


def test_영이하_점수_약속은_절대_비교다():
    # 0 이하에 배수를 곱하면 비교가 뒤집히거나 무의미하다.
    commitment = Commitment(_candidate(), committed_score=-1.0, committed_at=100.0)
    assert commitment.should_replace(0.5, now=110.0)
    assert not commitment.should_replace(-0.5, now=110.0)


# ── 점수 ────────────────────────────────────────────────────────────────────

def test_경로가_없으면_직선거리에_보정을_얹는다():
    weights = Weights(w_map=0.0, w_camera=0.0, w_dist=1.0, w_visit=0.0)
    with_path = _candidate(path_len_m=4.0)
    without = _candidate(path_len_m=None)   # 직선거리 1.0 × 1.5
    s_with = score(with_path, weights, from_x=0.0, from_y=0.0, history=[])
    s_without = score(without, weights, from_x=0.0, from_y=0.0, history=[])
    assert s_without == -1.5
    assert s_with == -4.0
    # 보정이 없으면(×1.0) 벽 뒤 후보가 체계적으로 가까워 보인다.
    assert s_without < -1.0


def test_방문_이력이_감점된다():
    weights = Weights(w_map=0.0, w_camera=0.0, w_dist=0.0, w_visit=2.0)
    candidate = _candidate()
    fresh = score(candidate, weights, from_x=0.0, from_y=0.0, history=[])
    revisit = score(candidate, weights, from_x=0.0, from_y=0.0, history=[(1.0, 0.0), (1.5, 0.0)])
    assert fresh == 0.0
    assert revisit == -4.0      # 반경 2m 안 이력 2건 × w_visit 2


def test_visit_penalty_반경():
    history = [(0.0, 0.0)]
    assert visit_penalty(history, 1.9, 0.0) == 1
    assert visit_penalty(history, 2.1, 0.0) == 0


def test_카메라_이득이_지도_이득보다_무겁다():
    # 수색이 컨셉이다 — 기본 가중치에서 같은 면적이면 사람을 볼 기회가 이긴다.
    weights = Weights()
    map_only = _candidate(map_gain_m2=1.0, camera_gain_m2=0.0, path_len_m=0.0)
    camera_only = _candidate(map_gain_m2=0.0, camera_gain_m2=1.0, path_len_m=0.0)
    s_map = score(map_only, weights, from_x=1.0, from_y=0.0, history=[])
    s_camera = score(camera_only, weights, from_x=1.0, from_y=0.0, history=[])
    assert s_camera > s_map


# ── 이득 계산 ────────────────────────────────────────────────────────────────

def test_이득은_물리_단위다():
    # 미지 절반(왼쪽), 자유 절반(오른쪽)인 2×2m 방. 중앙 후보의 지도 이득은
    # 대략 미지 면적과 같아야 한다 — 셀 수가 아니라 m² 로 나온다.
    size, res = 40, 0.05
    grid = np.zeros((size, size), dtype=np.int8)
    grid[:, : size // 2] = -1
    info = GridInfo(resolution=res, origin_x=0.0, origin_y=0.0, width=size, height=size)
    coverage = CameraCoverage()
    map_gain, camera_gain = compute_gains(grid, info, coverage, 1.0, 1.0, radius_m=0.5)
    # 반경 0.5m 창(1×1m)의 절반이 미지 → 약 0.5m². 창이 정사각형이라 오차 허용.
    assert 0.3 < map_gain < 0.8
    assert camera_gain > 0.0    # 아무것도 안 봤으니 자유 쪽 전부가 카메라 이득


def test_커버리지가_카메라_이득을_줄인다():
    size, res = 40, 0.05
    grid = np.zeros((size, size), dtype=np.int8)
    info = GridInfo(resolution=res, origin_x=0.0, origin_y=0.0, width=size, height=size)
    coverage = CameraCoverage()
    _, before = compute_gains(grid, info, coverage, 1.0, 1.0, radius_m=0.5)
    coverage.mark_area(1.0, 1.0, 0.6)
    _, after = compute_gains(grid, info, coverage, 1.0, 1.0, radius_m=0.5)
    assert after < before


def test_격자_밖_후보는_이득이_없다():
    grid = np.zeros((10, 10), dtype=np.int8)
    info = GridInfo(resolution=0.05, origin_x=0.0, origin_y=0.0, width=10, height=10)
    assert compute_gains(grid, info, CameraCoverage(), 99.0, 99.0) == (0.0, 0.0)

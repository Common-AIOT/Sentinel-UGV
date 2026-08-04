"""스윕 계획 시험 (S15P11A301-172).

방위 부채꼴 수학은 경계(±π, 랩어라운드)에서 조용히 틀리는 종류다 — 부호가
뒤집혀도 스윕은 돌아가고, 안 본 방향 하나가 영영 빠질 뿐이다. 경계를 값으로
못박는다.
"""

from __future__ import annotations

import math

from sentinel_exploration.sweep import (
    needed_sectors,
    plan_sweep,
    sector_center,
    sector_index,
    sweep_duration_s,
)


# ── 부채꼴 수학 ─────────────────────────────────────────────────────────────

def test_부채꼴_왕복이_일치한다():
    # index → center → index 가 항등이어야 랩어라운드가 맞다.
    for n in (8, 9, 12):
        for i in range(n):
            assert sector_index(sector_center(i, n), n) == i


def test_동쪽은_가운데_부채꼴이다():
    # 방위 0(+x)은 -π 에서 시작하는 9개 부채꼴의 5번째(index 4)다.
    assert sector_index(0.0, 9) == 4


def test_경계값_pi():
    # bearing == π 는 접혀서 마지막 부채꼴이다. floor 가 n 을 만들면 IndexError
    # 대신 조용히 잘못된 부채꼴로 가는 구현도 있어서 값으로 박는다.
    assert sector_index(math.pi, 9) == 8
    assert sector_index(-math.pi + 1e-9, 9) == 0


# ── 필요 부채꼴 ─────────────────────────────────────────────────────────────

def test_미관측_지점의_방위가_부채꼴을_켠다():
    needed = needed_sectors(0.0, 0.0, [(2.0, 0.0)], n_sectors=9, range_m=5.0)
    assert needed[4]            # 동쪽
    assert sum(needed) == 1


def test_유효거리_밖_지점은_무시한다():
    # 8m 밖 미관측 지점 때문에 드웰을 낭비하지 않는다 — 그 거리에선 탐지가
    # 안 되므로 돌아봐도 못 본다. 나중에 관측 목표(소스 B)로 소비된다.
    needed = needed_sectors(0.0, 0.0, [(8.0, 0.0)], n_sectors=9, range_m=5.0)
    assert not any(needed)


def test_사방에_있으면_전부_켜진다():
    points = [
        (math.cos(a) * 2.0, math.sin(a) * 2.0)
        for a in [sector_center(i, 9) for i in range(9)]
    ]
    assert all(needed_sectors(0.0, 0.0, points, n_sectors=9, range_m=5.0))


# ── 계획 ────────────────────────────────────────────────────────────────────

def test_정면_부채꼴은_계획에서_뺀다():
    # 도착 yaw 를 미관측 방향으로 잡으므로 정면은 도착 드웰이 이미 처리한다.
    needed = [False] * 9
    needed[4] = True            # 동쪽만 필요
    assert plan_sweep(0.0, needed) == []    # 이미 동쪽을 보고 있다


def test_아무것도_필요없으면_빈_계획이다():
    assert plan_sweep(0.0, [False] * 9) == []


def test_전부_필요하면_여덟_스텝이다():
    # 9개 중 정면 제외 8개.
    assert len(plan_sweep(0.0, [True] * 9)) == 8


def test_가까운_방위부터_돈다():
    needed = [False] * 9
    needed[5] = True            # 정면에서 +40°
    needed[0] = True            # 뒤쪽
    plan = plan_sweep(0.0, needed)
    assert len(plan) == 2
    assert abs(plan[0] - sector_center(5, 9)) < 1e-9   # 가까운 것 먼저


def test_랩어라운드_거리로_정렬한다():
    # 정면이 +170° 일 때 -170° 는 지구 반대편이 아니라 20° 옆이다.
    yaw = math.radians(170.0)
    needed = [False] * 9
    needed[sector_index(math.radians(-170.0), 9)] = True
    needed[sector_index(0.0, 9)] = True
    plan = plan_sweep(yaw, needed)
    # -170° 부채꼴이 0° 부채꼴보다 먼저다.
    first_sector = sector_index(plan[0], 9)
    assert first_sector == sector_index(math.radians(-170.0), 9)


def test_스윕_시간_추정():
    # 완전 360°(8스텝) ≈ 8 × (40°/0.5rad/s + 1.5s) ≈ 23초. 예산 감각을 시험으로
    # 박아 둔다 — 상수를 바꾸면 이 숫자가 함께 움직여야 한다.
    full = sweep_duration_s(8)
    assert 20.0 < full < 26.0


def test_한_바퀴_돈_방위도_같은_부채꼴이다():
    # yaw 가 적분 누적값으로 들어오면 ±π 를 벗어날 수 있다. 랩을 안 하면
    # 조용히 마지막 부채꼴로 접혀 안 본 방향 하나가 영영 빠진다.
    assert sector_index(2.0 * math.pi, 9) == sector_index(0.0, 9)
    assert sector_index(-2.0 * math.pi + 0.1, 9) == sector_index(0.1, 9)

"""도달 실패 블랙리스트 시험 (S15P11A301-172).

여기서 지키는 성질은 하나다 — **거부당하는 목표를 무한히 재선택하지 않는다.**

목표 선택은 점수 최댓값이고 점수는 지도에서 나온다. Nav2 가 목표를 거부하면
지도는 그대로이므로 다음 주기에 같은 후보가 다시 1위다. 그 목표로 또 보내고 또
거부당한다. 겉보기 증상은 "탐사가 도는데 로봇이 제자리"이고 로그에는 같은 좌표가
2초마다 반복해 찍힌다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_exploration.blacklist import GoalBlacklist  # noqa: E402


def test_not_blocked_before_limit():
    """상한 전에는 계속 시도한다. 한 번 실패가 영구 배제가 되면 안 된다.

    전역 계획은 일시적으로도 실패한다 — 지도가 아직 좁거나, costmap 이 막
    갱신되는 중이거나, 로봇이 벽에 붙어 서 있을 때다.
    """
    blacklist = GoalBlacklist(max_failures=3)

    assert blacklist.record_failure(1.0, 1.0) == 1
    assert not blacklist.is_blocked(1.0, 1.0)
    assert blacklist.record_failure(1.0, 1.0) == 2
    assert not blacklist.is_blocked(1.0, 1.0)


def test_blocked_at_limit():
    blacklist = GoalBlacklist(max_failures=3)

    for _ in range(3):
        blacklist.record_failure(1.0, 1.0)

    assert blacklist.is_blocked(1.0, 1.0)
    assert blacklist.blocked_count == 1


def test_nearby_failures_are_the_same_place():
    """frontier 대표점은 지도가 갱신될 때마다 몇 cm 움직인다.

    군집 식별자로 기억하면 같은 자리인데 다른 목표로 보여 블랙리스트가 새어
    나간다. 그래서 반경으로 묶는다.
    """
    blacklist = GoalBlacklist(radius_m=0.5, max_failures=3)

    blacklist.record_failure(1.00, 1.00)
    blacklist.record_failure(1.20, 1.00)   # 0.20m — 같은 자리
    count = blacklist.record_failure(1.00, 1.30)   # 0.30m — 같은 자리

    assert count == 3
    assert blacklist.is_blocked(1.10, 1.10)


def test_distant_failure_is_a_different_place():
    blacklist = GoalBlacklist(radius_m=0.5, max_failures=3)

    blacklist.record_failure(1.0, 1.0)
    blacklist.record_failure(1.0, 1.0)
    other = blacklist.record_failure(5.0, 5.0)

    assert other == 1
    assert not blacklist.is_blocked(5.0, 5.0)
    assert not blacklist.is_blocked(1.0, 1.0)   # 2회뿐이다


def test_first_failure_coordinate_is_kept():
    """실패 좌표를 갱신하면 반경 밖으로 걸어나갈 수 있다.

    0.4m 씩 이어서 실패하면, 매번 좌표를 옮기면 항목이 계속 따라가며 원래
    자리는 잊는다. 그래서 처음 실패한 지점을 유지한다.
    """
    blacklist = GoalBlacklist(radius_m=0.5, max_failures=3)

    blacklist.record_failure(0.0, 0.0)
    blacklist.record_failure(0.4, 0.0)
    blacklist.record_failure(0.8, 0.0)   # 처음 지점에서 0.8m — 새 항목이어야 한다

    assert blacklist.failure_count(0.0, 0.0) == 2
    assert blacklist.failure_count(0.8, 0.0) == 1


def test_success_clears_the_place():
    """도달했으면 이력을 지운다.

    같은 자리를 나중에 관측 후보로 다시 삼을 수 있고, 과거 실패가 남아 있으면
    한 번만 더 실패해도 상한에 닿는다.
    """
    blacklist = GoalBlacklist(max_failures=3)
    blacklist.record_failure(2.0, 2.0)
    blacklist.record_failure(2.0, 2.0)

    blacklist.record_success(2.0, 2.0)

    assert blacklist.failure_count(2.0, 2.0) == 0
    assert blacklist.record_failure(2.0, 2.0) == 1


def test_success_on_untouched_place_is_harmless():
    GoalBlacklist().record_success(9.0, 9.0)


def test_blocked_count_counts_only_blocked():
    """`blockedGoals` 가 status 로 나가는 값이다.

    DONE 인데 이 값이 크면 「다 봤다」가 아니라 「못 갔다」이므로, 실패가 쌓인
    자리만 세어야 한다.
    """
    blacklist = GoalBlacklist(max_failures=2)
    blacklist.record_failure(0.0, 0.0)          # 1회 — 아직 아니다
    blacklist.record_failure(5.0, 0.0)
    blacklist.record_failure(5.0, 0.0)          # 2회 — 차단

    assert blacklist.blocked_count == 1

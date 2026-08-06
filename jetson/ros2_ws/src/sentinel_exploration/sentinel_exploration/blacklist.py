#!/usr/bin/env python3
"""도달 실패한 목표를 임무 동안 기억한다 (S15P11A301-172).

## 없으면 로봇이 영원히 안 움직인다

목표 선택은 점수 최댓값이고 점수는 지도에서 나온다. Nav2 가 목표를 거부하면
(계획 불가·중간 실패) 지도는 그대로이므로 **다음 주기에 같은 후보가 다시 1위다.**
그 목표로 또 보내고 또 거부당한다. 겉보기 증상은 "탐사가 도는데 로봇이 제자리"이고,
로그에는 같은 좌표가 2초마다 반복해 찍힌다.

`allow_unknown: false` 와 겹치면 특히 잘 난다. frontier 는 정의상 미지 공간의
경계이므로, 목표점이 미지 셀 쪽으로 조금만 들어가도 전역 계획이 실패한다.

## 왜 좌표 반경으로 묶는가

frontier 군집은 지도가 갱신될 때마다 셀 구성이 바뀌고 대표점도 몇 cm 움직인다.
군집 식별자로 기억하면 **같은 자리인데 다른 목표로 보여** 블랙리스트가 새어 나간다.
그래서 위치 반경으로 판정한다. 반경은 목표 허용 오차(`xy_goal_tolerance` 0.15m)보다
커야 의미가 있다 — 그보다 작으면 "도달했다고 볼 거리"보다 좁은 범위를 구별하려는
셈이다.

## 왜 감쇠가 없는가

임무 탐사 상한이 7분(23.4)이다. 그 안에서 한 번 못 간 곳이 다시 갈 수 있게 되는
경우는 드물고, 있다면 지도가 넓어져 **다른 좌표의 새 후보**로 나타난다. 감쇠를
넣으면 실패를 반복하며 시간을 쓰는 쪽이 위험하다. 임무가 끝나면 노드가 이 객체를
버린다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class GoalBlacklist:
    """실패 횟수가 상한에 닿은 좌표를 차단한다."""

    radius_m: float = 0.5
    max_failures: int = 3

    # (x, y) -> 실패 횟수. 좌표는 처음 실패한 지점을 유지한다 — 갱신하면
    # 대표점이 조금씩 밀리며 반경 밖으로 걸어나갈 수 있다.
    _failures: list[list[float]] = field(default_factory=list)

    def record_failure(self, x: float, y: float) -> int:
        """실패를 적립하고 누적 횟수를 돌려준다."""
        entry = self._find(x, y)
        if entry is None:
            self._failures.append([x, y, 1.0])
            return 1
        entry[2] += 1.0
        return int(entry[2])

    def record_success(self, x: float, y: float) -> None:
        """도달했으면 그 자리의 실패 이력을 지운다.

        같은 자리를 나중에 다시 목표로 삼을 수 있고(관측 후보로), 과거 실패가
        남아 있으면 한 번만 더 실패해도 상한에 닿는다.
        """
        entry = self._find(x, y)
        if entry is not None:
            self._failures.remove(entry)

    def is_blocked(self, x: float, y: float) -> bool:
        entry = self._find(x, y)
        return entry is not None and entry[2] >= self.max_failures

    def failure_count(self, x: float, y: float) -> int:
        entry = self._find(x, y)
        return int(entry[2]) if entry is not None else 0

    @property
    def blocked_count(self) -> int:
        return sum(1 for entry in self._failures if entry[2] >= self.max_failures)

    def _find(self, x: float, y: float) -> list[float] | None:
        for entry in self._failures:
            if math.hypot(entry[0] - x, entry[1] - y) <= self.radius_m:
                return entry
        return None

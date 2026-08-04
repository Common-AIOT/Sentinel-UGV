"""점유격자 공통 수학 (S15P11A301-172).

rclpy 없이 시험 가능해야 한다. 좌표 규약은 nav2 그대로다 — `data`는 행 우선이고
**행 0이 아래**(origin이 좌하단 셀), 셀 값은 -1=미지, 0~100=점유 확률이다.

여기서는 화면이 아니라 수학을 다루므로 **행을 뒤집지 않는다.** 뒤집기는 그리는
쪽(관제 웹 `LiveMap`)의 일이다. 같은 규약을 두 곳에서 다르게 적용하면 frontier
좌표가 상하 반전된 채 그럴싸하게 틀린다 — 이 프로젝트에서 반복해서 겪은 형태다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 점유 판정 임계값. 관제 웹 `classifyGridCell`(occupancyGrid.ts)과 같은 값이어야
# 화면의 "벽"과 탐사의 "벽"이 일치한다.
OCCUPIED_THRESHOLD = 65


@dataclass(frozen=True)
class GridInfo:
    """`nav_msgs/OccupancyGrid`의 메타데이터만 뗀 것. numpy 배열과 함께 다닌다."""

    resolution: float
    origin_x: float
    origin_y: float
    width: int
    height: int


def world_to_cell(info: GridInfo, x: float, y: float) -> tuple[int, int] | None:
    """map 좌표(미터) → (row, col). 격자 밖이면 None.

    None 을 돌려주는 것이 의도다. 예외로 하면 레이캐스트처럼 격자 밖으로
    나가는 것이 정상 흐름인 호출자가 전부 try 로 감싸야 한다.
    """
    col = int((x - info.origin_x) / info.resolution)
    row = int((y - info.origin_y) / info.resolution)
    if 0 <= row < info.height and 0 <= col < info.width:
        return row, col
    return None


def cell_to_world(info: GridInfo, row: int, col: int) -> tuple[float, float]:
    """(row, col) → 셀 중심의 map 좌표(미터).

    모서리가 아니라 **중심**이다. 모서리로 하면 목표가 항상 반 셀 어긋나고,
    0.05m 격자에서는 눈에 안 보이지만 좌표 비교 시험이 흔들린다.
    """
    x = info.origin_x + (col + 0.5) * info.resolution
    y = info.origin_y + (row + 0.5) * info.resolution
    return x, y


def free_mask(grid: np.ndarray, occupied_threshold: int = OCCUPIED_THRESHOLD) -> np.ndarray:
    """자유 공간. 0 <= 값 < 임계.

    **음수를 먼저 걸러야 한다.** -1을 확률로 취급하면 임계 아래라 자유로
    분류되고, 그러면 미탐사 영역 전체가 통행 가능으로 계산된다.
    """
    return (grid >= 0) & (grid < occupied_threshold)


def unknown_mask(grid: np.ndarray) -> np.ndarray:
    return grid < 0


def occupied_mask(grid: np.ndarray, occupied_threshold: int = OCCUPIED_THRESHOLD) -> np.ndarray:
    return grid >= occupied_threshold

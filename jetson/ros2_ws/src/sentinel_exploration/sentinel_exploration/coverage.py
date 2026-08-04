"""카메라 커버리지 장부 (S15P11A301-172 설계 v2의 핵심).

라이다 커버리지(지도)와 카메라 커버리지는 다르다. 라이다는 360°·원거리라 방
중앙을 한 번 지나가면 지도가 완성되는데, 사람을 찾는 것은 전방 약 52° 카메라다.
frontier 만 좇으면 **지도는 완벽한데 구석에 쓰러진 사람은 화각에 한 번도 들어오지
않는** 결과가 난다. 이 장부가 그 구멍을 막는다.

## 좌표를 world 기준 정수로 든다

내부 상태는 `set[(i, j)]` 하나이고, 키는 `floor(x / cell_m)` 다. 격자 인덱스가
아니라 **world 좌표**를 쓰는 것이 요점이다.

- slam_toolbox 격자는 자라면서 origin 이 움직인다. 격자 인덱스로 들면 지도가
  자랄 때마다 장부를 재배치해야 한다.
- 루프 클로저가 지도를 다시 풀어도 world 좌표 오차는 보통 이 장부의 셀
  크기(0.25m)보다 작다. 어긋나더라도 "본 곳을 못 봤다"(과소) 방향으로 틀리므로
  다시 볼 뿐, 사람을 놓치는 방향이 아니다.
"""

from __future__ import annotations

import math

import numpy as np

from .grid import GridInfo, cell_to_world, free_mask, world_to_cell


class CameraCoverage:
    """카메라가 본 자유 공간의 장부. 순수 파이썬 — rclpy 없이 시험한다."""

    def __init__(self, cell_m: float = 0.25) -> None:
        self.cell_m = cell_m
        self._seen: set[tuple[int, int]] = set()

    def _key(self, x: float, y: float) -> tuple[int, int]:
        return (math.floor(x / self.cell_m), math.floor(y / self.cell_m))

    def is_seen(self, x: float, y: float) -> bool:
        return self._key(x, y) in self._seen

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def mark_visible(
        self,
        grid: np.ndarray,
        info: GridInfo,
        x: float,
        y: float,
        yaw: float,
        *,
        hfov_rad: float,
        range_m: float,
        ray_step_deg: float = 2.0,
        min_range_m: float = 0.3,
    ) -> int:
        """현재 자세에서 카메라 절두체를 레이캐스트해 본 셀을 기록한다.

        호출자(노드)가 각속도 게이트를 책임진다 — 회전 중 프레임은 블러로 탐지
        신뢰도가 떨어지므로 커버리지로 인정하지 않는다. 여기서 거르지 않는
        이유는 이 함수가 자세만 받지 속도를 모르기 때문이다.

        레이는 세 가지에서 멈춘다. 점유 셀(**벽 너머를 봤다고 기록하면 안
        된다**), 미지 셀(시야가 트였는지 알 수 없다 — 과소 방향으로 보수적),
        격자 밖. 반환값은 새로 기록된 셀 수다.

        레이는 `min_range_m` 부터 시작한다. **로봇 발밑 셀은 자유가 아닐 때가
        많다** — 라이다가 차체 일부·근접물을 점유로 찍거나 미지로 남는다. 0m
        부터 검사하면 모든 레이가 첫 셀에서 끊겨 커버리지가 영원히 0이다(실기동
        스모크에서 실제로 그랬다: 발밑 값 100). 그 반경 안은 로봇이 서 있는
        자리라 사람이 있을 수 없으니 건너뛰어도 과대 기록이 아니다.
        """
        marked = 0
        half = hfov_rad / 2.0
        step = info.resolution * 0.9  # 셀보다 약간 짧게 — 대각선 건너뜀 방지
        n_rays = max(2, int(math.degrees(hfov_rad) / ray_step_deg) + 1)
        free = free_mask(grid)

        for angle in np.linspace(yaw - half, yaw + half, n_rays):
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            distance = min_range_m
            while distance <= range_m:
                cell = world_to_cell(info, x + distance * cos_a, y + distance * sin_a)
                if cell is None:
                    break
                row, col = cell
                if not free[row, col]:
                    # 점유(벽)든 미지든 이 너머는 본 것으로 치지 않는다. 벽은
                    # 물리적으로 막고, 미지는 트였는지 알 수 없다 — 과소 방향.
                    break
                key = self._key(*cell_to_world(info, row, col))
                if key not in self._seen:
                    self._seen.add(key)
                    marked += 1
                distance += step
        return marked

    def mark_area(self, x: float, y: float, radius_m: float) -> None:
        """encounter 확정 지점 주변을 관측 완료로 마킹한다.

        같은 사람에게 반복해서 스윕을 돌지 않기 위한 것이다. 중복 encounter
        판정 자체는 mission_manager 의 몫이고(FR-009), 여기는 탐사가 그 자리를
        다시 관측 목표로 삼지 않게만 한다.
        """
        steps = int(radius_m / self.cell_m) + 1
        for i in range(-steps, steps + 1):
            for j in range(-steps, steps + 1):
                px = x + i * self.cell_m
                py = y + j * self.cell_m
                if (px - x) ** 2 + (py - y) ** 2 <= radius_m * radius_m:
                    self._seen.add(self._key(px, py))

    def unseen_free(
        self, grid: np.ndarray, info: GridInfo, *, stride: int | None = None
    ) -> list[tuple[float, float]]:
        """자유인데 카메라가 아직 못 본 지점들(world 좌표).

        지도 격자를 장부 셀 크기 간격(stride)으로 성기게 훑는다. 전 셀을 다
        보면 같은 장부 셀을 다섯 번씩 조회할 뿐이다.
        """
        if stride is None:
            stride = max(1, int(self.cell_m / info.resolution))
        free = free_mask(grid)
        result: list[tuple[float, float]] = []
        for row in range(0, info.height, stride):
            for col in range(0, info.width, stride):
                if not free[row, col]:
                    continue
                x, y = cell_to_world(info, row, col)
                if not self.is_seen(x, y):
                    result.append((x, y))
        return result

    def unseen_free_area_m2(self, grid: np.ndarray, info: GridInfo) -> float:
        """미관측 자유 면적(m²). 2단 종료 판정의 좌변이다."""
        return len(self.unseen_free(grid, info)) * self.cell_m * self.cell_m


def observation_candidates(
    unseen: list[tuple[float, float]],
    *,
    cluster_dist_m: float = 1.0,
    min_area_m2: float = 0.25,
    cell_m: float = 0.25,
) -> list[tuple[float, float]]:
    """미관측 지점들을 군집화해 관측 목표(viewpoint)를 만든다 — 목표 소스 B.

    frontier 가 소진돼도 이 후보가 남아 있으면 탐사는 끝나지 않는다. 지도
    완성과 수색 완료는 다른 사건이다.

    단순 그리드 BFS 군집화다. 반환은 각 군집의 centroid — 로봇이 거기 서서
    스윕하면 군집 전체가 관측 반경 안에 든다는 가정이며, 군집이 관측 반경보다
    크면 스윕 후 남은 지점이 다음 선택 주기에 다시 후보로 나온다. 즉 한 번에
    완벽할 필요가 없다.
    """
    if not unseen:
        return []
    min_points = max(1, int(min_area_m2 / (cell_m * cell_m)))
    keys = {(math.floor(x / cluster_dist_m), math.floor(y / cluster_dist_m)) for x, y in unseen}
    by_key: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in unseen:
        by_key.setdefault((math.floor(x / cluster_dist_m), math.floor(y / cluster_dist_m)), []).append((x, y))

    visited: set[tuple[int, int]] = set()
    candidates: list[tuple[float, float]] = []
    for start in sorted(keys):
        if start in visited:
            continue
        component: list[tuple[float, float]] = []
        stack = [start]
        visited.add(start)
        while stack:
            key = stack.pop()
            component.extend(by_key[key])
            i, j = key
            for neighbor in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if neighbor in keys and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        if len(component) < min_points:
            continue
        xs = sum(p[0] for p in component) / len(component)
        ys = sum(p[1] for p in component) / len(component)
        candidates.append((xs, ys))
    return candidates

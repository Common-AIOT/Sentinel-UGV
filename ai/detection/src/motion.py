"""trackId별 움직임 추적 — 부동(inactivity) 신호.

문헌의 쓰러짐 탐지는 형상·자세·**운동**·깊이 네 종류 신호를 조합한다. 분류기가
"하강 속도, 최종 몸 방향, **바닥에 머문 시간**"을 함께 가중하는 것이 표준 구성이다.
우리는 형상과 자세만 쓰고 있었고 운동 신호가 빠져 있었다.

이 모듈은 그중 **부동 시간**을 담당한다. 추가 센서 없이 trackId와 bbox 이력만으로
계산되며, 관절이 하나도 안 잡혀도 동작한다. 누운 사람은 관절이 가려지지만
움직이지 않는다는 성질을 쓴다.

## 단독으로 쓰러짐을 만들지 않는다

가만히 서 있는 사람도 부동이다. 부동만으로 FALLEN을 만들면 서서 대기하는 사람이
전부 쓰러진 것이 된다. 이 점수는 **형상이 수평일 때 확신을 올리는 보조**로만 쓰며,
가중치를 낮게 두어 단독으로는 임계값을 넘지 못하게 한다(PostureClassifier).

## 카메라가 움직이면 사람도 움직인 것처럼 보인다

UGV는 주행하며 촬영하므로 카메라 이동이 bbox 이동으로 나타난다. 그래서 이동량을
**bbox 크기로 정규화**한다. 사람이 화면에서 자기 몸 크기만큼 움직였는지를 보므로
거리에 따른 편차도 함께 줄어든다. 다만 카메라 팬을 사람의 움직임과 구분하지는
못한다. 주행 중에는 부동 점수가 낮게 나오는 쪽으로 틀리며, 이는 오탐을 만들지
않는 안전한 방향이다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _MotionState:
    # 마지막으로 관측한 bbox 중심과 크기
    center: tuple[float, float]
    size: float
    last_seen: float
    # 움직임이 임계 미만인 상태가 시작된 시각. 움직이면 갱신된다.
    still_since: float
    # 직전 프레임에서 계산한 정규화 이동량(디버깅·튜닝용)
    last_displacement: float = 0.0


class MotionTracker:
    """trackId별로 "얼마나 오래 가만히 있었는지"를 잰다."""

    def __init__(
        self,
        *,
        still_ratio: float = 0.06,
        full_still_seconds: float = 3.0,
        forget_seconds: float = 10.0,
    ) -> None:
        # 이 비율(bbox 크기 대비) 미만으로 움직이면 "정지"로 본다.
        self.still_ratio = still_ratio
        # 이 시간(초) 이상 정지해 있으면 부동 점수가 1.0이 된다.
        self.full_still_seconds = max(1e-6, full_still_seconds)
        self.forget_seconds = forget_seconds
        self._states: dict[int, _MotionState] = {}

    def update(
        self,
        track_id: int | None,
        center: tuple[float, float],
        size: float,
        timestamp_sec: float,
    ) -> float:
        """관측을 반영하고 부동 점수(0~1)를 돌려준다.

        추적 ID가 없으면 이력을 이을 수 없으므로 0을 반환한다. 판정에 기여하지
        않을 뿐이며, 값을 지어내지 않는다.
        """
        if track_id is None:
            return 0.0

        state = self._states.get(track_id)
        if state is None:
            # 처음 본 사람은 아직 정지 이력이 없다. 0에서 시작한다.
            self._states[track_id] = _MotionState(
                center=center, size=size, last_seen=timestamp_sec, still_since=timestamp_sec
            )
            return 0.0

        # 오래 사라졌다 온 사람은 그 사이를 "가만히 있었다"고 볼 수 없다.
        if timestamp_sec - state.last_seen > self.forget_seconds:
            state.still_since = timestamp_sec

        scale = max(size, state.size, 1e-6)
        moved = (
            abs(center[0] - state.center[0]) ** 2 + abs(center[1] - state.center[1]) ** 2
        ) ** 0.5
        displacement = moved / scale
        state.last_displacement = displacement

        if displacement > self.still_ratio:
            # 움직였다. 정지 구간을 다시 시작한다.
            state.still_since = timestamp_sec

        state.center = center
        state.size = size
        state.last_seen = timestamp_sec

        still_sec = max(0.0, timestamp_sec - state.still_since)
        return min(1.0, still_sec / self.full_still_seconds)

    def still_seconds(self, track_id: int | None, timestamp_sec: float) -> float:
        """현재까지 정지해 있던 시간(초). 로그·디버깅용."""
        if track_id is None:
            return 0.0
        state = self._states.get(track_id)
        if state is None:
            return 0.0
        return max(0.0, timestamp_sec - state.still_since)

    def prune(self, timestamp_sec: float) -> None:
        """오래 안 보인 트랙의 이력을 지운다."""
        limit = self.forget_seconds * 2
        for tid in [t for t, s in self._states.items() if timestamp_sec - s.last_seen > limit]:
            del self._states[tid]

    def reset(self) -> None:
        self._states.clear()

"""trackId 단위 지속 시간 관리 + ID 교체 시 상태 승계.

**이벤트 트리거는 "사람을 안정적으로 관측한 시간"이다.** 자세가 아니다.
프로젝트 명세를 따른다.
  docs/01-프로젝트-개요.md:156 "YOLO와 ByteTrack이 한 명 이상의 사람을 약 1초간
  안정적으로 확인하면 encounter를 생성하고 탐사를 일시정지한다"
  docs/04-자율주행.md 8.1 "person confidence ≥ threshold AND ByteTrack 약 1초
  안정 관측 → 탐사 일시정지·encounter 후보 생성"

재난 현장에서는 서 있거나 앉아 있는 요구조자도 구조 대상이므로, 쓰러진 사람만
보고하면 사람을 찾고도 알리지 않는 false negative가 된다(AGENTS.md §23).

자세는 트리거가 아니라 **속성**이다. `possible_fallen` 지속 시간은 심각도 표시용으로
따로 재서 페이로드에 싣는다(명세 DB의 detections.pose_status에 대응).

ByteTrack/BoT-SORT는 사람이 가려지면 새 trackId를 부여하므로, 시간·위치의 단순
조건으로 상태를 승계한다(docs/07-AI-탐지.md 25.1이 허용하는 범위. 외형은 쓰지 않는다).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import POSTURE_FALLEN


@dataclass
class TrackPersistence:
    """한 트랙의 현재 지속 상태."""

    # 사람을 연속으로 관측한 시간(초). encounter 트리거 기준.
    seen_sec: float = 0.0
    # possible_fallen이 연속된 시간(초). 심각도 표시용이며 트리거가 아니다.
    fallen_sec: float = 0.0
    # 이번 프레임에 encounter가 확정되었는지.
    event_confirmed: bool = False


@dataclass
class _TrackState:
    # 사람이 연속으로 보이기 시작한 시각(초).
    seen_since: float | None = None
    # possible_fallen이 시작된 시각(초). 자세가 바뀌면 None으로 되돌린다.
    fallen_since: float | None = None
    last_seen: float = 0.0
    # 마지막으로 이벤트를 발행한 시각(초). 쿨다운 판정에 쓴다.
    last_event_at: float | None = None
    # 마지막 관측 위치(bbox 중심)와 크기. ID가 바뀌었을 때 승계 판단에 쓴다.
    last_center: tuple[float, float] | None = None
    last_size: float = 0.0


class PersistenceTracker:
    def __init__(
        self,
        *,
        person_confirm_seconds: float = 1.0,
        fallen_seconds: float = 1.0,
        forget_seconds: float = 10.0,
        event_cooldown_seconds: float = 15.0,
        inherit_distance_ratio: float = 1.0,
    ) -> None:
        self.person_confirm_seconds = person_confirm_seconds
        self.fallen_seconds = fallen_seconds
        # 사람을 기억하는 유일한 시간 기준. 승계 허용 시간, 관측 연속성 판정,
        # 상태 폐기가 모두 이 값을 쓴다. 트래커의 track_buffer도 여기서 환산된다.
        self.forget_seconds = forget_seconds
        self.event_cooldown_seconds = event_cooldown_seconds
        self.inherit_distance_ratio = inherit_distance_ratio
        self._states: dict[int, _TrackState] = {}

    def _find_inheritable(
        self, center: tuple[float, float], size: float, timestamp_sec: float
    ) -> _TrackState | None:
        """최근에 끊긴 트랙 중 같은 자리에 있던 것을 찾는다. 시간과 위치만 본다."""
        best: _TrackState | None = None
        best_dist = float("inf")
        for state in self._states.values():
            if state.seen_since is None or state.last_center is None:
                continue
            gap = timestamp_sec - state.last_seen
            # 아직 살아 있는 트랙(gap<=0)이나 너무 오래된 트랙은 승계 대상이 아니다.
            if gap <= 0 or gap > self.forget_seconds:
                continue
            scale = max(size, state.last_size, 1.0)
            dist = math.hypot(center[0] - state.last_center[0], center[1] - state.last_center[1])
            if dist <= scale * self.inherit_distance_ratio and dist < best_dist:
                best, best_dist = state, dist
        return best

    def update(
        self,
        track_id: int | None,
        posture_status: str,
        timestamp_sec: float,
        *,
        center: tuple[float, float] | None = None,
        size: float = 0.0,
    ) -> TrackPersistence:
        """관측 하나를 반영하고 현재 지속 상태를 반환한다.

        timestamp_sec는 파일 입력이면 영상 내 시간, 카메라면 벽시계 경과 시간이다.
        """
        if track_id is None:
            # 추적 ID가 없으면 누구의 시간인지 알 수 없으므로 누적하지 않는다.
            return TrackPersistence()

        state = self._states.get(track_id)
        if state is None:
            state = _TrackState()
            # 새 ID다. 방금 끊긴 트랙이 같은 자리에 있었다면 지속 시간을 이어받는다.
            if center is not None:
                donor = self._find_inheritable(center, size, timestamp_sec)
                if donor is not None:
                    state.seen_since = donor.seen_since
                    state.fallen_since = donor.fallen_since
                    state.last_event_at = donor.last_event_at
                    # 승계 후 원본은 비워 중복 승계를 막는다.
                    donor.seen_since = None
                    donor.fallen_since = None
                    donor.last_center = None
            self._states[track_id] = state

        if center is not None:
            state.last_center = center
            state.last_size = size

        # forget_seconds를 넘겨 사라졌다가 같은 ID로 돌아오면 "잊은" 것으로 본다.
        # 그 안에 돌아오면 같은 사람으로 보고 누적을 이어간다. 규칙은 이 하나뿐이다.
        if state.seen_since is not None and (timestamp_sec - state.last_seen) > self.forget_seconds:
            state.seen_since = None
            state.fallen_since = None
            state.last_event_at = None

        state.last_seen = timestamp_sec
        if state.seen_since is None:
            state.seen_since = timestamp_sec
        seen = timestamp_sec - state.seen_since

        # 자세 지속 시간은 트리거가 아니라 속성으로만 잰다.
        if posture_status == POSTURE_FALLEN:
            if state.fallen_since is None:
                state.fallen_since = timestamp_sec
            fallen = timestamp_sec - state.fallen_since
        else:
            state.fallen_since = None
            fallen = 0.0

        if seen < self.person_confirm_seconds:
            return TrackPersistence(seen_sec=seen, fallen_sec=fallen)

        # 쿨다운 중이면 재발행하지 않는다(명세 25.4의 중복 이벤트 제거를 로컬에서 1차 억제).
        if (
            state.last_event_at is not None
            and timestamp_sec - state.last_event_at < self.event_cooldown_seconds
        ):
            return TrackPersistence(seen_sec=seen, fallen_sec=fallen)

        state.last_event_at = timestamp_sec
        return TrackPersistence(seen_sec=seen, fallen_sec=fallen, event_confirmed=True)

    def is_fallen_confirmed(self, fallen_sec: float) -> bool:
        """자세가 심각도 기준을 넘었는지. 이벤트 트리거가 아니라 표시용이다."""
        return fallen_sec >= self.fallen_seconds

    def prune(self, timestamp_sec: float) -> None:
        """잊기로 한 시간이 지난 트랙 상태를 정리한다.

        forget_seconds보다 여유를 두고 지운다. 정확히 forget_seconds에 지우면
        승계 판정이 실행되기 직전에 후보가 사라질 수 있다.
        """
        limit = self.forget_seconds * 1.5
        expired = [
            tid for tid, s in self._states.items() if timestamp_sec - s.last_seen > limit
        ]
        for tid in expired:
            del self._states[tid]

    def reset(self) -> None:
        self._states.clear()

"""keypoint 기반 규칙 자세 판정.

학습 모델이 아니라 명시적인 규칙이다(AGENTS.md §10). 단일 기준으로 판정하지 않고
최소 두 개 이상의 신호를 조합한다(AGENTS.md §15). 임계값은 configs/pipeline.yaml에서
주입받으며 이 파일에 하드코딩하지 않는다.

사용하는 신호
  1. torso_angle_deg      : 어깨중점→엉덩이중점 벡터가 수직축과 이루는 각도
  2. bbox_aspect_ratio    : bbox 가로/세로 비
  3. vertical_extent_ratio: 어깨-엉덩이 y 차이를 사람 크기로 정규화한 값
"""

from __future__ import annotations

import math
from collections import Counter, deque

from .schemas import (
    POSTURE_STANDING,
    POSTURE_POSSIBLE_FALLEN,
    POSTURE_UNKNOWN,
    Detection,
    PoseResult,
    PostureResult,
)


class PostureClassifier:
    def __init__(
        self,
        *,
        torso_horizontal_deg: float = 55.0,
        bbox_aspect_ratio: float = 1.2,
        vertical_extent_ratio: float = 0.25,
        min_valid_keypoints: int = 4,
        keypoint_confidence: float = 0.5,
    ) -> None:
        self.torso_horizontal_deg = torso_horizontal_deg
        self.bbox_aspect_ratio = bbox_aspect_ratio
        self.vertical_extent_ratio = vertical_extent_ratio
        self.min_valid_keypoints = min_valid_keypoints
        self.keypoint_confidence = keypoint_confidence

    @staticmethod
    def _midpoint(
        a: tuple[float, float] | None, b: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        """양쪽이 다 있으면 중점, 한쪽만 있으면 그 점을 쓴다."""
        if a and b:
            return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        return a or b

    def classify(self, detection: Detection, pose: PoseResult | None) -> PostureResult:
        signals: dict[str, float | bool | None] = {}

        # bbox 신호는 pose가 없어도 계산할 수 있다.
        height = detection.height
        aspect = detection.width / height if height > 0 else 0.0
        signals["bbox_aspect_ratio"] = round(aspect, 3)
        bbox_horizontal = aspect >= self.bbox_aspect_ratio

        if pose is None:
            return PostureResult(
                status=POSTURE_UNKNOWN,
                signals=signals,
                reason="pose 결과 없음",
            )

        valid = pose.valid_count(self.keypoint_confidence)
        signals["valid_keypoints"] = valid
        if valid < self.min_valid_keypoints:
            return PostureResult(
                status=POSTURE_UNKNOWN,
                signals=signals,
                reason=f"유효 keypoint 부족 ({valid} < {self.min_valid_keypoints})",
            )

        conf = self.keypoint_confidence
        shoulder = self._midpoint(
            pose.get("left_shoulder", conf), pose.get("right_shoulder", conf)
        )
        hip = self._midpoint(pose.get("left_hip", conf), pose.get("right_hip", conf))

        if shoulder is None or hip is None:
            # 상체 축을 못 구하면 bbox 신호 하나만 남는다.
            # 신호가 하나뿐이면 확정하지 않는다(§15 최소 2개 조합).
            return PostureResult(
                status=POSTURE_UNKNOWN,
                signals=signals,
                reason="어깨 또는 엉덩이 keypoint 없음",
            )

        dx = hip[0] - shoulder[0]
        dy = hip[1] - shoulder[1]
        torso_len = math.hypot(dx, dy)
        if torso_len < 1e-6:
            return PostureResult(
                status=POSTURE_UNKNOWN,
                signals=signals,
                reason="상체 길이가 0에 가까움",
            )

        # 수직축(0, 1) 기준 각도. 0도=수직(서 있음), 90도=수평(누움).
        torso_angle = math.degrees(math.atan2(abs(dx), abs(dy)))
        signals["torso_angle_deg"] = round(torso_angle, 2)
        torso_horizontal = torso_angle >= self.torso_horizontal_deg

        # 어깨-엉덩이의 수직 거리를 사람 크기로 정규화. 누우면 작아진다.
        scale = max(height, torso_len)
        extent = abs(dy) / scale if scale > 0 else 0.0
        signals["vertical_extent_ratio"] = round(extent, 3)
        extent_horizontal = extent <= self.vertical_extent_ratio

        votes = [torso_horizontal, bbox_horizontal, extent_horizontal]
        fallen_votes = sum(votes)
        signals["fallen_votes"] = fallen_votes

        # 세 신호 중 두 개 이상이 수평을 가리키면 possible_fallen.
        if fallen_votes >= 2:
            return PostureResult(
                status=POSTURE_POSSIBLE_FALLEN,
                signals=signals,
                reason=f"수평 신호 {fallen_votes}/3",
            )

        return PostureResult(
            status=POSTURE_STANDING,
            signals=signals,
            reason=f"수평 신호 {fallen_votes}/3",
        )


class PostureSmoother:
    """자세 판정의 프레임 간 흔들림을 완충한다(hysteresis).

    누운 사람은 팔다리가 몸에 가려져 keypoint가 한두 프레임씩 부족해지고,
    그때마다 자세가 unknown으로 튄다. 완충이 없으면 그 순간 fallen 누적이 끊겨
    이벤트를 놓치거나 화면이 계속 깜빡인다.

    규칙
      1. 최근 window 프레임의 다수결로 자세를 정한다.
      2. unknown_yields_to_known이면, 창 안에 확정 상태(normal/possible_fallen)가
         하나라도 있을 때 unknown은 후보에서 제외한다. 전부 unknown일 때만 unknown이다.

    trackId별로 이력을 관리하므로 사람이 섞이지 않는다.
    """

    def __init__(self, *, window: int = 5, unknown_yields_to_known: bool = True) -> None:
        self.window = max(1, window)
        self.unknown_yields_to_known = unknown_yields_to_known
        self._history: dict[int, deque[str]] = {}

    def smooth(self, track_id: int | None, result: PostureResult) -> PostureResult:
        """원본 판정을 받아 완충된 판정을 반환한다.

        원본 상태는 signals["raw_status"]에 남겨 임계값 조정 시 근거로 쓴다.
        """
        if self.window == 1 or track_id is None:
            # 완충 비활성이거나 추적 ID가 없으면 그대로 통과시킨다.
            return result

        hist = self._history.setdefault(track_id, deque(maxlen=self.window))
        hist.append(result.status)

        candidates = list(hist)
        if self.unknown_yields_to_known:
            known = [s for s in candidates if s != POSTURE_UNKNOWN]
            if known:
                candidates = known

        status, _ = Counter(candidates).most_common(1)[0]
        if status == result.status:
            return result

        signals = dict(result.signals)
        signals["raw_status"] = result.status
        signals["window"] = list(hist)
        return PostureResult(
            status=status,
            signals=signals,
            reason=f"{result.reason} → 완충 적용({result.status}→{status})",
        )

    def forget(self, track_ids: set[int]) -> None:
        """더 이상 추적하지 않는 트랙의 이력을 지운다."""
        for tid in [t for t in self._history if t not in track_ids]:
            del self._history[tid]

    def reset(self) -> None:
        self._history.clear()

"""IoU 기반 사람 추적 (S15P11A301-136, 명세 25.4).

ROS도 YOLO도 모른다. 박스 목록을 받아 `trackId`를 붙여 돌려준다. 시간을 주입하므로
카메라 없이 시험할 수 있다.

## ByteTrack이 아닌 이유

25.4가 `trackId`를 ByteTrack 단위로 정의했지만 같은 절이 "짧은 가림으로 trackId가
바뀌면 시간·위치·외형의 단순 조건으로 병합하되 **정밀 재식별은 범위에서 제외**한다"고
정했다. 즉 id 안정성은 요구사항이 아니다.

IoU 매칭만으로도 25.2의 "동일 track이 약 1초 동안 최소 관측 횟수를 만족한다"를 판정할
수 있다. 30fps에서 사람은 프레임 간 크게 움직이지 않으므로 겹침이 충분하다.

id가 바뀌면 `mission_manager`가 새 track으로 보고 `personCount`를 늘린다. 그것이
줄어들지 않는 값이므로(32-6 그룹 처리) 한 사람이 둘로 세어질 수 있다. 이 한계를
감수하는 대신 IoU 문턱을 낮게 두어 id 교체를 줄인다.

## 놓친 프레임을 바로 버리지 않는다

`max_misses`만큼 유지한다. 사람이 잠깐 가려지거나 탐지가 한 프레임 실패했을 때 track을
지우면 다음 프레임에서 새 id가 발급되고, 25.2의 1초 안정성이 처음부터 다시 시작한다.
그러면 사람이 앞에 있는데도 확정이 계속 미뤄진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .detector import Box, Detection

# 같은 사람으로 볼 최소 겹침. 낮게 두면 다른 사람을 같은 track으로 합치고, 높게
# 두면 조금만 움직여도 새 id가 발급된다. 30fps에서 걷는 사람은 프레임당 겹침이
# 0.7 이상이므로 0.3은 여유가 있다.
DEFAULT_IOU_THRESHOLD = 0.3
# 이 횟수만큼 연속으로 못 봐도 track을 유지한다. 추론 주기 5Hz에서 3이면 0.6초다.
DEFAULT_MAX_MISSES = 3


@dataclass
class Track:
    """추적 중인 사람 하나."""

    track_id: int
    box: Box
    confidence: float
    first_seen_at: float
    last_seen_at: float
    # 지금까지 관측된 횟수. 25.2의 "최소 관측 횟수"를 판정하는 값이다.
    hits: int = 1
    # 연속으로 놓친 횟수. 갱신되면 0으로 돌아간다.
    misses: int = 0
    # 관측 시각 목록. 25.2의 "약 1초 동안"을 창 단위로 판정하려면 언제 봤는지
    # 알아야 한다. hits만으로는 1초에 10번 본 것과 10초에 10번 본 것을 구별할 수
    # 없다.
    seen_at: list[float] = field(default_factory=list)

    def observations_within(self, now: float, window_seconds: float) -> int:
        return sum(1 for stamp in self.seen_at if now - stamp <= window_seconds)


class IouTracker:
    """겹침으로 프레임 간 사람을 잇는다."""

    def __init__(
        self,
        *,
        iou_threshold: float = DEFAULT_IOU_THRESHOLD,
        max_misses: int = DEFAULT_MAX_MISSES,
        history_seconds: float = 3.0,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        # 관측 시각을 무한정 쌓지 않는다. 5분 이벤트에서 5Hz면 1500개가 되고,
        # 판정에 쓰는 창은 1초뿐이다.
        self.history_seconds = history_seconds
        self.tracks: dict[int, Track] = {}
        self._next_id = 1

    def update(self, detections: list[Detection], now: float) -> list[Track]:
        """이번 프레임의 탐지로 track을 갱신한다. 살아 있는 track을 돌려준다."""
        unmatched = list(range(len(detections)))
        matched_tracks: set[int] = set()

        # 겹침이 큰 쌍부터 붙인다. 순서대로 처리하면 먼저 나온 track이 엉뚱한
        # 탐지를 가져갈 수 있다.
        pairs: list[tuple[float, int, int]] = []
        for track_id, track in self.tracks.items():
            for index in unmatched:
                overlap = track.box.iou(detections[index].box)
                if overlap >= self.iou_threshold:
                    pairs.append((overlap, track_id, index))
        pairs.sort(reverse=True)

        used_detections: set[int] = set()
        for _, track_id, index in pairs:
            if track_id in matched_tracks or index in used_detections:
                continue
            detection = detections[index]
            track = self.tracks[track_id]
            track.box = detection.box
            track.confidence = detection.confidence
            track.last_seen_at = now
            track.hits += 1
            track.misses = 0
            track.seen_at.append(now)
            matched_tracks.add(track_id)
            used_detections.add(index)

        # 붙지 않은 탐지는 새 사람이다.
        for index, detection in enumerate(detections):
            if index in used_detections:
                continue
            track_id = self._next_id
            self._next_id += 1
            self.tracks[track_id] = Track(
                track_id=track_id,
                box=detection.box,
                confidence=detection.confidence,
                first_seen_at=now,
                last_seen_at=now,
                seen_at=[now],
            )
            matched_tracks.add(track_id)

        # 갱신되지 않은 track은 놓친 것으로 센다.
        for track_id, track in list(self.tracks.items()):
            if track_id not in matched_tracks:
                track.misses += 1
                if track.misses > self.max_misses:
                    del self.tracks[track_id]
                    continue
            track.seen_at = [
                stamp
                for stamp in track.seen_at
                if now - stamp <= self.history_seconds
            ]

        return list(self.tracks.values())

    def visible(self) -> list[Track]:
        """이번 프레임에 실제로 보인 track만.

        `misses`가 0인 것이다. 놓친 track을 후보로 발행하면 사람이 없는데도
        `mission_manager`가 이벤트를 이어간다.
        """
        return [track for track in self.tracks.values() if track.misses == 0]

    def reset(self) -> None:
        self.tracks.clear()

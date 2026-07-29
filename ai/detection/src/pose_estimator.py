"""pretrained YOLO Pose 추론.

person crop을 입력으로 받고, keypoint를 **원본 프레임 좌표계로 복원**해서 반환한다.
crop 좌표를 그대로 내보내면 overlay가 엉뚱한 위치에 그려지므로 반드시 복원한다
(AGENTS.md §10, 게이트 4번).

이번 스프린트에서 Pose는 추론만 수행하고 학습하지 않는다(AGENTS.md §14).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from ultralytics import YOLO

from .schemas import Detection, PoseResult, PostureResult


@dataclass
class _PoseTrackState:
    consecutive: int = 0
    last_run_at: float | None = None
    last_seen: float = 0.0
    # 마지막으로 Pose가 실행된 프레임의 판정 결과. 실행하지 않는 프레임에서 재사용한다.
    cached_posture: PostureResult | None = field(default=None)


class PoseScheduler:
    """조건부 Pose 실행 스케줄러.

    프로젝트 명세 25.6을 따른다(docs/04-자율주행-AI.md:78).
      "사람 탐지는 추가 학습 YOLO26n Detect(상시 약 15FPS),
       자세 보조 판정은 YOLO26n Pose 조건부 실행
       (사람 3프레임 이상 연속 감지 시 활성, 약 2FPS,
        동일 자세 약 1.5초 유지 시 이벤트, 3초 미감지 시 중단)"

    매 프레임 사람 수만큼 Pose를 돌리면 Jetson에서 Detect FPS를 유지할 수 없다.
    Detect가 사람 발견을 책임지고 Pose는 자세 정보만 보조한다(명세 420행).

    실행하지 않는 프레임에서는 직전 판정을 재사용한다. 그래야 자세가 깜빡이지 않는다.
    """

    def __init__(
        self,
        *,
        activate_after_frames: int = 3,
        max_fps: float = 2.0,
        deactivate_after_seconds: float = 3.0,
        min_bbox_width: float = 80.0,
        min_bbox_height: float = 80.0,
    ) -> None:
        self.activate_after_frames = activate_after_frames
        self.min_interval = 1.0 / max_fps if max_fps > 0 else 0.0
        self.deactivate_after_seconds = deactivate_after_seconds
        self.min_bbox_width = min_bbox_width
        self.min_bbox_height = min_bbox_height
        self._states: dict[int, _PoseTrackState] = {}

    def should_run(self, detection: Detection, timestamp_sec: float) -> bool:
        """이번 프레임에 이 사람에 대해 Pose를 실행할지 판단한다."""
        # bbox가 너무 작으면 keypoint를 신뢰할 수 없다(ISSUE-01 초기값).
        if detection.width < self.min_bbox_width or detection.height < self.min_bbox_height:
            return False

        tid = detection.track_id
        if tid is None:
            # 추적 ID가 없으면 연속성을 셀 수 없다. 명세의 "3프레임 연속" 조건을
            # 만족시킬 방법이 없으므로 실행하지 않는다.
            return False

        state = self._states.get(tid)
        if state is None:
            state = _PoseTrackState()
            self._states[tid] = state

        # 3초 이상 안 보였으면 연속성을 초기화한다(명세의 "3초 미감지 시 중단").
        if timestamp_sec - state.last_seen > self.deactivate_after_seconds:
            state.consecutive = 0
            state.cached_posture = None
            state.last_run_at = None

        state.last_seen = timestamp_sec
        state.consecutive += 1

        if state.consecutive < self.activate_after_frames:
            return False
        if state.last_run_at is not None and (timestamp_sec - state.last_run_at) < self.min_interval:
            return False

        state.last_run_at = timestamp_sec
        return True

    def cache(self, track_id: int | None, posture: PostureResult) -> None:
        if track_id is not None and track_id in self._states:
            self._states[track_id].cached_posture = posture

    def cached(self, track_id: int | None) -> PostureResult | None:
        if track_id is None:
            return None
        state = self._states.get(track_id)
        return state.cached_posture if state else None

    def prune(self, timestamp_sec: float) -> None:
        limit = self.deactivate_after_seconds * 2
        for tid in [t for t, s in self._states.items() if timestamp_sec - s.last_seen > limit]:
            del self._states[tid]

    def reset(self) -> None:
        self._states.clear()


class PoseEstimator:
    def __init__(
        self,
        model_path: str,
        *,
        confidence: float = 0.5,
        crop_margin: float = 0.1,
        device: str | None = None,
        imgsz: int = 640,
        quantize: int | str | None = None,
    ) -> None:
        self.model_path = model_path
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.crop_margin = crop_margin
        self.device = device
        self.imgsz = imgsz
        self.quantize = quantize

    def _crop_box(
        self, detection: Detection, frame_w: int, frame_h: int
    ) -> tuple[int, int, int, int] | None:
        """여유(margin)를 준 crop 영역을 프레임 범위로 clip해서 반환한다."""
        x1, y1, x2, y2 = detection.bbox_xyxy
        mx = detection.width * self.crop_margin
        my = detection.height * self.crop_margin

        cx1 = max(0, int(round(x1 - mx)))
        cy1 = max(0, int(round(y1 - my)))
        cx2 = min(frame_w, int(round(x2 + mx)))
        cy2 = min(frame_h, int(round(y2 + my)))

        if cx2 - cx1 < 2 or cy2 - cy1 < 2:
            return None
        return cx1, cy1, cx2, cy2

    def estimate(self, frame: np.ndarray, detection: Detection) -> PoseResult | None:
        """detection이 가리키는 사람 한 명의 keypoint를 추정한다.

        반환 좌표는 원본 frame 기준 절대 픽셀이다.
        """
        frame_h, frame_w = frame.shape[:2]
        box = self._crop_box(detection, frame_w, frame_h)
        if box is None:
            return None
        cx1, cy1, cx2, cy2 = box

        # 원본을 수정하지 않도록 복사본을 넘긴다(AGENTS.md §10 storage 원칙과 동일).
        crop = frame[cy1:cy2, cx1:cx2].copy()

        results = self.model.predict(
            crop,
            conf=self.confidence,
            device=self.device,
            imgsz=self.imgsz,
            quantize=self.quantize,
            verbose=False,
        )
        if not results:
            return None

        keypoints = getattr(results[0], "keypoints", None)
        if keypoints is None or keypoints.xy is None or len(keypoints.xy) == 0:
            return None

        # crop 안에 여러 사람이 잡히면 가장 신뢰도가 높은 하나만 쓴다.
        xy_all = keypoints.xy.cpu().numpy()
        conf_all = (
            keypoints.conf.cpu().numpy()
            if keypoints.conf is not None
            else np.ones(xy_all.shape[:2], dtype=float)
        )
        best = int(np.argmax(conf_all.mean(axis=1)))
        xy = xy_all[best]
        conf = conf_all[best]

        # crop 좌표 → 원본 프레임 좌표 복원
        restored: list[tuple[float, float]] = []
        for (x, y), c in zip(xy, conf):
            if c <= 0 and x == 0 and y == 0:
                # 미검출 keypoint는 (0, 0)으로 나온다. 복원하지 않고 그대로 둔다.
                restored.append((0.0, 0.0))
            else:
                restored.append((float(x) + cx1, float(y) + cy1))

        return PoseResult(keypoints_xy=restored, keypoints_conf=[float(c) for c in conf])

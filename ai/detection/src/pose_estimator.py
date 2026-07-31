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

    ## 예산이 두 겹이다 (2026-07-30 추가)

    명세의 "약 2 FPS"는 **파이프라인 전체의 Pose 예산**이다(명세 431행 "전체 화면 분석").
    track별로만 제한하면 사람 N명일 때 초당 2N회가 되어 명세의 N배를 쓴다. Jetson
    실측(S15P11A301-150)에서 사람 4명 조건일 때 기대 약 127회 대비 실제 300회가 기록됐다.

    그래서 제한을 두 겹으로 둔다.
      1. track별 간격 — 한 사람의 자세가 과하게 자주 갱신되지 않게 한다
      2. **전역 간격** — 전체 Pose 실행 횟수를 max_fps로 묶는다

    전역 예산은 자격을 갖춘 track들에 **라운드로빈**으로 배분한다. 가장 오래 갱신되지
    않은 사람이 먼저 차례를 받으므로, 특정 한 명만 계속 갱신되고 나머지가 굶는 일이 없다.

    crop 방식은 유지한다. 명세 문언은 "전체 화면 분석"이지만 전체 화면 Pose는 멀리 있는
    작은 사람의 keypoint 품질을 떨어뜨린다. person false negative를 최우선 리스크로
    두는 원칙(AGENTS.md §23)에 따라 **예산만 명세에 맞추고 crop은 유지**한다.
    이 해석 차이는 AGENTS.md §0에 기록한다.
    """

    def __init__(
        self,
        *,
        activate_after_frames: int = 3,
        max_fps: float = 2.0,
        deactivate_after_seconds: float = 3.0,
        min_bbox_width: float = 80.0,
        min_bbox_height: float = 80.0,
        global_budget: bool = True,
    ) -> None:
        self.activate_after_frames = activate_after_frames
        self.min_interval = 1.0 / max_fps if max_fps > 0 else 0.0
        self.deactivate_after_seconds = deactivate_after_seconds
        self.min_bbox_width = min_bbox_width
        self.min_bbox_height = min_bbox_height
        # False로 두면 예전 동작(track별 예산만)으로 되돌아간다. A/B 비교용이다.
        self.global_budget = global_budget
        self._states: dict[int, _PoseTrackState] = {}
        # 전역 예산의 마지막 실행 시각. track과 무관하게 하나만 유지한다.
        self._last_global_run_at: float | None = None

    def _budget_available(self, timestamp_sec: float) -> bool:
        """전역 예산이 이번 시점에 Pose 1회를 허용하는지."""
        if not self.global_budget or self.min_interval <= 0:
            return True
        if self._last_global_run_at is None:
            return True
        return (timestamp_sec - self._last_global_run_at) >= self.min_interval

    def _is_eligible(self, detection: Detection, timestamp_sec: float) -> bool:
        """track 자체 조건(크기·연속 감지·자기 간격)을 만족하는지.

        전역 예산은 보지 않는다. 라운드로빈 후보를 고를 때도 쓰인다.
        """
        if detection.width < self.min_bbox_width or detection.height < self.min_bbox_height:
            return False
        tid = detection.track_id
        if tid is None:
            return False
        state = self._states.get(tid)
        if state is None:
            return False
        if state.consecutive < self.activate_after_frames:
            return False
        if state.last_run_at is not None and (timestamp_sec - state.last_run_at) < self.min_interval:
            return False
        return True

    def select(self, detections: list[Detection], timestamp_sec: float) -> set[int]:
        """이번 프레임에 Pose를 돌릴 trackId 집합을 정한다.

        프레임 단위로 한 번 호출한다. 전역 예산이 있으면 자격자 중
        **가장 오래 갱신되지 않은 한 명**만 고른다(라운드로빈).
        """
        # 연속 감지 카운트와 last_seen은 자격 판정보다 먼저 갱신되어야 한다.
        for det in detections:
            self._touch(det, timestamp_sec)

        eligible = [d for d in detections if self._is_eligible(d, timestamp_sec)]
        if not eligible:
            return set()

        if not self.global_budget or self.min_interval <= 0:
            chosen = eligible
        elif not self._budget_available(timestamp_sec):
            return set()
        else:
            # 마지막 실행이 가장 오래된 track에 차례를 준다. 한 번도 안 돈 track이 최우선.
            def staleness(det: Detection) -> float:
                last = self._states[det.track_id].last_run_at
                return float("inf") if last is None else timestamp_sec - last

            chosen = [max(eligible, key=staleness)]

        for det in chosen:
            self._states[det.track_id].last_run_at = timestamp_sec
        if chosen:
            self._last_global_run_at = timestamp_sec
        return {d.track_id for d in chosen}

    def _touch(self, detection: Detection, timestamp_sec: float) -> None:
        """track 상태를 이번 프레임 기준으로 갱신한다(연속 감지 카운트·last_seen)."""
        tid = detection.track_id
        if tid is None:
            return
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

    def should_run(self, detection: Detection, timestamp_sec: float) -> bool:
        """이번 프레임에 이 사람에 대해 Pose를 실행할지 판단한다.

        단일 탐지만 보는 경로다. 전역 예산을 라운드로빈으로 배분하려면 프레임 전체를
        봐야 하므로 파이프라인은 select()를 쓴다. 이 메서드는 전역 예산을 선착순으로
        적용하며, 기존 호출부·테스트 호환을 위해 남겨 둔다.
        """
        # bbox가 너무 작으면 keypoint를 신뢰할 수 없다(ISSUE-01 초기값).
        if detection.width < self.min_bbox_width or detection.height < self.min_bbox_height:
            return False

        # 추적 ID가 없으면 연속성을 셀 수 없다. 명세의 "3프레임 연속" 조건을
        # 만족시킬 방법이 없으므로 실행하지 않는다.
        if detection.track_id is None:
            return False

        self._touch(detection, timestamp_sec)

        if not self._is_eligible(detection, timestamp_sec):
            return False
        if not self._budget_available(timestamp_sec):
            return False

        self._states[detection.track_id].last_run_at = timestamp_sec
        self._last_global_run_at = timestamp_sec
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
        self._last_global_run_at = None


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

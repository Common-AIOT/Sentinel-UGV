"""YOLO Detect + ByteTrack.

모델 경로는 반드시 인자로 받는다. 사전학습 가중치와 파인튜닝 가중치를
인자만 바꿔 교체할 수 있어야 한다(AGENTS.md §26 실행 순서 변경, 게이트 11번).

person 필터링은 class id가 아니라 class name으로 판정한다. 다음 스프린트에
클래스가 추가되면 id는 재배치되지만 이름은 안정적이다(AGENTS.md §11).
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np
from ultralytics import YOLO

from .schemas import Detection


class ObjectDetector:
    def __init__(
        self,
        model_path: str,
        *,
        target_classes: Iterable[str] = ("person",),
        confidence: float = 0.5,
        iou: float = 0.7,
        tracker: str = "bytetrack.yaml",
        device: str | None = None,
        imgsz: int = 640,
        quantize: int | str | None = None,
    ) -> None:
        self.model_path = model_path
        self.model = YOLO(model_path)
        self.target_classes = {c.lower() for c in target_classes}
        self.confidence = confidence
        self.iou = iou
        self.tracker = tracker
        self.device = device
        # Jetson에서 추론 해상도와 정밀도는 FPS에 가장 크게 영향을 준다.
        # quantize: 16/"fp16"이면 FP16, None이면 FP32. (구 half 인자는 deprecated)
        self.imgsz = imgsz
        self.quantize = quantize

    @property
    def class_names(self) -> dict[int, str]:
        """모델이 가진 class id → name 매핑. 클래스 확장 시 단일 출처로 쓴다."""
        return self.model.names

    def _resolve_target_ids(self) -> list[int] | None:
        """target_classes에 해당하는 class id 목록.

        모델이 해당 이름을 하나도 갖고 있지 않으면 None을 반환해 필터를 걸지 않고,
        호출부에서 이름 기준으로 다시 거른다.
        """
        ids = [i for i, name in self.class_names.items() if str(name).lower() in self.target_classes]
        return ids or None

    def detect(self, frame: np.ndarray, *, persist: bool = True) -> list[Detection]:
        """한 프레임에서 대상 클래스를 탐지하고 trackId를 부여한다.

        persist=True는 이전 프레임의 추적 상태를 이어간다. 영상 순차 처리에서는
        항상 True여야 하며, 독립된 이미지 배치에서는 False로 둔다.
        """
        results = self.model.track(
            frame,
            persist=persist,
            tracker=self.tracker,
            conf=self.confidence,
            iou=self.iou,
            classes=self._resolve_target_ids(),
            device=self.device,
            imgsz=self.imgsz,
            quantize=self.quantize,
            verbose=False,
        )
        if not results:
            return []
        return self._parse(results[0])

    def _parse(self, result: Any) -> list[Detection]:
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []

        names = result.names if getattr(result, "names", None) else self.class_names
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        # 추적이 아직 ID를 부여하지 못한 프레임에서는 boxes.id가 None이다.
        ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else None

        detections: list[Detection] = []
        for i in range(len(xyxy)):
            class_id = int(clss[i])
            class_name = str(names.get(class_id, class_id)).lower()
            # classes 필터가 적용되지 않았을 경우를 대비해 이름으로 한 번 더 거른다.
            if class_name not in self.target_classes:
                continue
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            detections.append(
                Detection(
                    class_id=class_id,
                    class_name=class_name,
                    confidence=float(confs[i]),
                    bbox_xyxy=(x1, y1, x2, y2),
                    track_id=int(ids[i]) if ids is not None else None,
                )
            )
        return detections

    def reset_tracker(self) -> None:
        """새 영상을 처리하기 전에 추적 상태를 초기화한다.

        초기화하지 않으면 이전 영상의 trackId가 이어져 중복 이벤트의 원인이 된다.
        """
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None and hasattr(predictor, "trackers"):
            for tracker in predictor.trackers:
                if hasattr(tracker, "reset"):
                    tracker.reset()

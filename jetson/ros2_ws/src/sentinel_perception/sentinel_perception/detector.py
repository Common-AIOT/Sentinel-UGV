"""YOLO 사람 탐지 (S15P11A301-136, 명세 25.1·25.2).

ROS를 모른다. numpy 배열을 받아 박스를 돌려준다. 그래서 카메라나 브로커 없이
이미지 파일 하나로 단독 시험할 수 있다.

## `ai/detection`으로 옮길 것을 전제한 경계

도영훈님이 S15P11A301-99~102에서 추론 모듈을 만들면 이 파일은 그것을 호출하는
얇은 껍데기가 된다. 지금 이 파일 안에 ultralytics 호출이 있는 것은 그 모듈이 아직
없기 때문이며, 계약(`person-candidates.schema.json`)과 ROS 경계는 그대로 유지된다.

## person만 남긴다

COCO 80종 중 나머지는 쓰지 않는다. 25.2가 "class가 person이다"를 확정 조건으로
정했고, 다른 클래스를 발행하면 `mission_manager`가 사람으로 오해한다.

`classes=[0]`으로 모델 단계에서 걸러 후처리 비용을 줄인다. COCO에서 0이 person이다.

## FP32를 쓰지 않는다

Orin Nano는 CPU와 GPU가 RAM을 공유한다. 여유가 적을 때 FP32 추론은
`CUBLAS_STATUS_ALLOC_FAILED`로 시작조차 못 한다(`jetson/models/README.md`).
그래서 `quantize='fp16'`을 명시한다. `half=True`는 ultralytics 8.4에서
deprecated이며 앞으로 제거된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# COCO 클래스 인덱스. 25.2가 person만 확정 대상으로 정했다.
PERSON_CLASS_ID = 0

DEFAULT_MODEL_PATH = 'jetson/models/yolo26n.pt'
DEFAULT_IMAGE_SIZE = 640
# 25.2의 "confidence가 설정값 이상이다". 최종값은 TBD-CAL-001이며 시연 환경
# 검증셋으로 조정한다. 낮게 두면 오탐 클립이 생기고(30.6 장단점 표) 높게 두면
# 어두운 곳의 사람을 놓친다.
DEFAULT_CONFIDENCE = 0.5


@dataclass(frozen=True)
class Box:
    """이미지 좌표 바운딩 박스. 좌상단 기준 픽셀 단위다.

    `person-candidates.schema.json`의 `box`와 같은 형태를 쓴다. 변환 지점을
    하나로 줄이면 계약이 어긋날 자리가 줄어든다.
    """

    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def iou(self, other: Box) -> float:
        """겹침 비율. 추적에서 같은 사람인지 판단하는 데 쓴다."""
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        if right <= left or bottom <= top:
            return 0.0
        overlap = (right - left) * (bottom - top)
        union = self.area + other.area - overlap
        return overlap / union if union > 0 else 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            'x': round(self.x, 1),
            'y': round(self.y, 1),
            'width': round(self.width, 1),
            'height': round(self.height, 1),
        }


@dataclass(frozen=True)
class Detection:
    """한 프레임에서 찾은 사람 하나. 아직 track이 아니다."""

    box: Box
    confidence: float


class PersonDetector:
    """ultralytics YOLO를 사람 탐지에만 쓴다.

    모델 로딩이 수 초 걸리므로 생성 시 한 번만 한다. 노드가 뜬 뒤 첫 프레임에서
    로딩하면 그 프레임이 몇 초 지연되고, 그동안 들어온 프레임이 큐에 쌓인다.
    """

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        *,
        image_size: int = DEFAULT_IMAGE_SIZE,
        confidence: float = DEFAULT_CONFIDENCE,
        device: int | str = 0,
        warmup_frames: int = 2,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f'모델 가중치가 없다: {self.model_path}. '
                'jetson/models/README.md의 등록된 가중치를 확인한다'
            )

        # import를 여기서 한다. ultralytics가 torch를 끌어오므로 import만으로도
        # 수 초와 수백 MB를 쓴다. 이 모듈을 import하는 시험이 그 비용을 물지
        # 않도록 클래스를 만들 때까지 미룬다.
        from ultralytics import YOLO

        self.image_size = image_size
        self.confidence = confidence
        self.device = device
        self._model = YOLO(str(self.model_path))

        if warmup_frames > 0:
            # 첫 호출은 CUDA 커널 컴파일로 훨씬 느리다. 실측에서 워밍업 전과 후가
            # 자릿수 단위로 달랐다. 노드가 뜬 직후의 프레임을 버리지 않으려면
            # 여기서 미리 태워야 한다.
            blank = np.zeros((image_size, image_size, 3), dtype=np.uint8)
            for _ in range(warmup_frames):
                self._predict(blank)

    def _predict(self, image: np.ndarray) -> Any:
        return self._model.predict(
            image,
            imgsz=self.image_size,
            conf=self.confidence,
            classes=[PERSON_CLASS_ID],
            # FP32는 Orin Nano에서 메모리 부족으로 시작조차 못 할 수 있다.
            quantize='fp16',
            verbose=False,
            device=self.device,
        )[0]

    def detect(self, image: np.ndarray) -> list[Detection]:
        """BGR 이미지에서 사람을 찾는다.

        `classes=[0]`으로 걸렀으므로 결과는 전부 person이다. 그래도 클래스를 다시
        확인하지 않는 이유는, 확인하려면 `names`를 매 프레임 조회해야 하고 모델이
        바뀌면 그 인덱스도 함께 바뀌기 때문이다. 필터를 한 곳에만 둔다.
        """
        result = self._predict(image)
        boxes = getattr(result, 'boxes', None)
        if boxes is None:
            return []

        detections: list[Detection] = []
        # xywh는 중심 기준이므로 xyxy에서 직접 만든다. 좌상단 기준을 쓰는 이유는
        # 계약(person-candidates.schema.json)이 그렇게 정의돼 있기 때문이다.
        for row, score in zip(
            boxes.xyxy.tolist(), boxes.conf.tolist(), strict=False
        ):
            x1, y1, x2, y2 = row
            detections.append(
                Detection(
                    box=Box(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
                    confidence=float(score),
                )
            )
        return detections

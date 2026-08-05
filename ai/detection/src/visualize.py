"""overlay 시각화.

원본 프레임을 수정하지 않는다. 항상 복사본에 그린 뒤 반환한다(AGENTS.md §10).
"""

from __future__ import annotations

import cv2
import numpy as np

from .schemas import POSTURE_FALLEN, Detection, PersonObservation

# COCO 17 keypoint 골격 연결. 시각 확인용이며 판정에는 쓰지 않는다.
SKELETON = (
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
)

_COLOR_NORMAL = (0, 200, 0)
_COLOR_FALLEN = (0, 0, 255)
_COLOR_KEYPOINT = (255, 200, 0)
# 장애물은 사람과 확실히 구분되어야 한다. 회색 얇은 실선으로 배경처럼 그린다.
# 사람이 초록/빨강 굵은 선이므로 시선을 뺏지 않는다.
_COLOR_OBSTACLE = (160, 160, 160)


def draw_obstacles(canvas: np.ndarray, obstacles: list[Detection]) -> None:
    """장애물을 canvas에 직접 그린다. 사람보다 먼저 그려 뒤에 깔리게 한다.

    ⚠️ 이 표시는 주행 안전 근거가 아니다. 명세는 물리 장애물 회피를 LiDAR/Nav2에
    맡긴다(docs/01-프로젝트-개요.md:136). 화면에 안 뜬다고 로봇이 못 피하는 것이
    아니고, 뜬다고 로봇이 그것을 피하는 것도 아니다.
    """
    for det in obstacles:
        x1, y1, x2, y2 = (int(round(v)) for v in det.bbox_xyxy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), _COLOR_OBSTACLE, 1)
        cv2.putText(
            canvas, det.class_name, (x1, max(12, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, _COLOR_OBSTACLE, 1,
        )


def _status_color(status: str) -> tuple[int, int, int]:
    """이진 라벨이므로 두 색만 쓴다. 판정 확신도는 라벨이 아니라 점수로 표시한다."""
    return _COLOR_FALLEN if status == POSTURE_FALLEN else _COLOR_NORMAL


def draw(
    frame: np.ndarray,
    persons: list[PersonObservation],
    *,
    keypoint_confidence: float = 0.5,
    obstacles: list[Detection] | None = None,
) -> np.ndarray:
    """관측 결과를 그린 새 프레임을 반환한다."""
    canvas = frame.copy()

    # 사람보다 먼저 그려 뒤에 깔리게 한다. 겹쳐도 사람이 가려지지 않는다.
    if obstacles:
        draw_obstacles(canvas, obstacles)

    for person in persons:
        det = person.detection
        color = _status_color(person.posture.status)
        x1, y1, x2, y2 = (int(round(v)) for v in det.bbox_xyxy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

        track_label = f"ID{det.track_id}" if det.track_id is not None else "ID-"
        # seen은 encounter 트리거 기준, fallen은 자세 심각도 속성이다.
        # 점수와 신호 수를 함께 띄운다. 이진 라벨만 보면 아슬아슬한 판정과
        # 확실한 판정이 구분되지 않고, 관절 없이 내린 판정도 티가 안 난다.
        posture = person.posture
        label = (
            f"{track_label} {posture.status} {posture.fallen_score:.2f}"
            f"/{posture.signal_count} {det.confidence:.2f} seen{person.seen_sec:.1f}s"
        )
        if person.fallen_sec > 0:
            label += f" fallen{person.fallen_sec:.1f}s"
        if person.event_confirmed:
            label += " [EVENT]"

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ty = max(0, y1 - th - 4)
        cv2.rectangle(canvas, (x1, ty), (x1 + tw + 4, ty + th + 4), color, -1)
        cv2.putText(
            canvas, label, (x1 + 2, ty + th),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA,
        )

        if person.pose is None:
            continue

        pose = person.pose
        for i, (x, y) in enumerate(pose.keypoints_xy):
            if pose.keypoints_conf[i] < keypoint_confidence:
                continue
            cv2.circle(canvas, (int(round(x)), int(round(y))), 3, _COLOR_KEYPOINT, -1)

        for a, b in SKELETON:
            if a >= len(pose.keypoints_xy) or b >= len(pose.keypoints_xy):
                continue
            if (
                pose.keypoints_conf[a] < keypoint_confidence
                or pose.keypoints_conf[b] < keypoint_confidence
            ):
                continue
            pa = tuple(int(round(v)) for v in pose.keypoints_xy[a])
            pb = tuple(int(round(v)) for v in pose.keypoints_xy[b])
            cv2.line(canvas, pa, pb, _COLOR_KEYPOINT, 2)

    return canvas

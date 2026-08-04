"""파이프라인 전반에서 주고받는 데이터 구조.

관제 보고 페이로드는 프로젝트 통신 명세(docs/05-통신-서버-영상.md 31-5, 31-6)의
공통 봉투와 ENCOUNTER_CONFIRMED 구조를 따른다. 현재 detection 단독으로 채울 수 없는
필드(mapPose, encounterId, missionId 등)는 None으로 두되 구조는 명세와 일치시킨다.
값을 지어내지 않는다(AGENTS.md §31).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# COCO pose 17 keypoint 순서. Ultralytics pose 모델의 출력 순서와 같다.
KEYPOINT_NAMES = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
KEYPOINT_INDEX = {name: i for i, name in enumerate(KEYPOINT_NAMES)}

# 자세 상태. 2026-08-03에 3값에서 **2값(이진)** 으로 변경했다.
#
# 재난 탐색에서 관제가 필요한 답은 "쓰러졌나 아닌가" 하나다. 이전 3값 체계는
# POSE_UNKNOWN이 관측의 약 21%를 차지해 다섯 중 하나는 답을 내지 못했다.
#
# FALLEN은 "누워 있는 형태"를 뜻한다. 직전에 서 있었든 앉아 있었든 무관하게
# 현재 형태가 수평이면 FALLEN이다.
#
# 판정 확신도는 라벨이 아니라 fallen_score(0~1)와 signal_count(쓴 신호 수)로
# 따로 싣는다. 세 번째 라벨을 만들지 않고도 근거의 두께를 전달한다.
#
# ⚠️ 명세 개정 사항이다. docs/07-AI-탐지-음성.md 25.6이 규정하던
#    STANDING / POSSIBLE_FALLEN / POSE_UNKNOWN 3값을 대체한다.
#    POSSIBLE_FALLEN → FALLEN 개명은 확신을 높인다는 뜻이 아니다. 확신도는
#    라벨이 아니라 fallen_score로 싣는다. "의료적 판정으로 쓰지 않는다"는
#    명세 25.6의 원칙은 그대로 유지한다.
POSTURE_NORMAL = "NORMAL"
POSTURE_FALLEN = "FALLEN"


def utc_now_iso() -> str:
    """ISO 8601 UTC 문자열. 명세 31-5의 sentAt 형식과 맞춘다."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_uuid() -> str:
    return str(uuid.uuid4())


@dataclass
class Detection:
    """Detect 단계의 사람 한 명."""

    class_id: int
    class_name: str
    confidence: float
    # 원본 프레임 좌표계의 절대 픽셀 [x1, y1, x2, y2]
    bbox_xyxy: tuple[float, float, float, float]
    # ByteTrack이 부여한 추적 ID. 추적이 아직 안정화되지 않으면 None일 수 있다.
    track_id: int | None = None

    @property
    def width(self) -> float:
        return self.bbox_xyxy[2] - self.bbox_xyxy[0]

    @property
    def height(self) -> float:
        return self.bbox_xyxy[3] - self.bbox_xyxy[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox_xyxy": [round(v, 2) for v in self.bbox_xyxy],
            "track_id": self.track_id,
        }


@dataclass
class PoseResult:
    """Pose 추론 결과. 좌표는 반드시 원본 프레임 기준으로 복원된 값이다."""

    keypoints_xy: list[tuple[float, float]]
    keypoints_conf: list[float]

    def get(self, name: str, min_conf: float) -> tuple[float, float] | None:
        """이름으로 keypoint를 조회한다. confidence 미달이면 None."""
        idx = KEYPOINT_INDEX.get(name)
        if idx is None or idx >= len(self.keypoints_xy):
            return None
        if self.keypoints_conf[idx] < min_conf:
            return None
        return self.keypoints_xy[idx]

    def valid_count(self, min_conf: float) -> int:
        return sum(1 for c in self.keypoints_conf if c >= min_conf)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keypoints_xy": [[round(x, 2), round(y, 2)] for x, y in self.keypoints_xy],
            "keypoints_conf": [round(c, 4) for c in self.keypoints_conf],
        }


@dataclass
class PostureResult:
    """규칙 기반 자세 판정 결과."""

    status: str
    # 쓰러짐 점수 0~1. 임계값을 넘으면 status가 FALLEN이 된다.
    # ⚠️ 보정된 확률이 아니다. "임계값에서 얼마나 떨어져 있나"를 0~1로 편 값이며,
    # 0.7이 "이런 경우의 70%가 실제 쓰러짐"을 뜻하지 않는다. 라벨 데이터 확보 후
    # 로지스틱 회귀로 교체하면 그때 보정된 확률이 된다.
    fallen_score: float = 0.0
    # 점수 계산에 실제로 기여한 신호 수(1~4). 관절이 없으면 형상·운동만 남아 2가 된다.
    # 라벨은 이진이지만 이 값으로 근거의 두께를 전달한다.
    signal_count: int = 0
    # 판정에 사용된 개별 신호. 임계값 조정(ISSUE-06) 시 근거로 쓴다.
    signals: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "fallen_score": round(self.fallen_score, 4),
            "signal_count": self.signal_count,
            "signals": self.signals,
            "reason": self.reason,
        }


@dataclass
class PersonObservation:
    """한 프레임에서 관측한 사람 한 명의 통합 결과."""

    detection: Detection
    pose: PoseResult | None
    posture: PostureResult
    # 사람을 연속 관측한 시간. encounter 트리거 기준(명세 25.1).
    seen_sec: float
    # possible_fallen 지속 시간. 트리거가 아니라 심각도 속성.
    fallen_sec: float
    event_confirmed: bool
    # 자세가 심각도 기준을 넘겼는지(표시용).
    fallen_confirmed: bool = False
    # 이번 프레임에 Pose를 실제로 실행했는지. False면 posture는 직전 판정의 재사용값이다.
    # 명세 778행: "pose_status는 조건부 Pose가 실행된 관측에만 기록한다"
    pose_ran: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "detection": self.detection.to_dict(),
            "pose": self.pose.to_dict() if self.pose else None,
            "posture": self.posture.to_dict(),
            "seen_sec": round(self.seen_sec, 3),
            "fallen_sec": round(self.fallen_sec, 3),
            "event_confirmed": self.event_confirmed,
            "fallen_confirmed": self.fallen_confirmed,
            "pose_ran": self.pose_ran,
        }


@dataclass
class FrameResult:
    """한 프레임 처리 결과."""

    frame_index: int
    timestamp: str
    source: str
    persons: list[PersonObservation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "frame_index": self.frame_index,
            "source": self.source,
            "persons": [p.to_dict() for p in self.persons],
        }


def build_envelope(
    message_type: str,
    data: dict[str, Any],
    *,
    schema_version: str,
    robot_id: str,
    mission_id: str | None,
    sequence: int,
) -> dict[str, Any]:
    """명세 31-5 공통 JSON 봉투.

    missionId는 Mission Manager가 발급하므로 미연동 상태에서는 None이다.
    """
    return {
        "schemaVersion": schema_version,
        "messageId": new_uuid(),
        "messageType": message_type,
        "robotId": robot_id,
        "missionId": mission_id,
        "sequence": sequence,
        "sentAt": utc_now_iso(),
        "data": data,
    }


def build_encounter_data(
    persons: list[PersonObservation],
    *,
    encounter_id: str,
) -> dict[str, Any]:
    """명세 31-6 ENCOUNTER_CONFIRMED의 data 블록.

    detection 단독으로 확보할 수 없는 필드는 None으로 둔다.
    - mapPose: SLAM/Nav2의 TF 변환 필요 (명세 25.2)
    - recordingState / preBufferSec: 이벤트 녹화 파이프라인 필요 (명세 32-6)
    이 필드들은 통합 시 cloud_bridge_node 또는 상위 노드가 채운다.
    """
    return {
        "encounterId": encounter_id,
        "mapPose": None,
        "personCount": len(persons),
        "persons": [
            {
                "trackId": p.detection.track_id,
                "confidence": round(p.detection.confidence, 4),
                # 명세 detections 테이블의 pose_status 컬럼에 대응한다.
                # 자세는 encounter의 트리거가 아니라 속성이다(명세 25.1, 25.6).
                # 2026-08-03부터 NORMAL / FALLEN 이진값이다.
                "poseStatus": p.posture.status,
                # 쓰러짐 점수(0~1)와 판정에 쓴 신호 수. 라벨이 이진이라 확신도가
                # 사라지는 것을 막는다. signalCount가 작으면 관절 없이 형상·부동만으로
                # 판정했다는 뜻이므로 관제가 참고 수준으로 다룰 수 있다.
                # ⚠️ fallenScore는 보정된 확률이 아니다(AGENTS.md §15).
                "fallenScore": round(p.posture.fallen_score, 3),
                "signalCount": p.posture.signal_count,
                # FALLEN 지속 시간. 관제가 심각도를 판단할 근거로 함께 보낸다.
                "fallenSec": round(p.fallen_sec, 2) if p.fallen_sec > 0 else None,
                "observedSec": round(p.seen_sec, 2),
            }
            for p in persons
        ],
        "recordingState": None,
        "preBufferSec": None,
    }

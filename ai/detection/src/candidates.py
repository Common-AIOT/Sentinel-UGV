"""`/perception/person_candidates` 본문 생성 (S15P11A301-133 계약).

계약 원문은 `common/schemas/person-candidates.schema.json`이다. 이 모듈은
ROS 의존이 없어 단위 테스트로 계약 형식을 검증한다(tests/test_candidates.py).

계약 요점:

- 후보가 없어도 빈 `candidates` 배열을 주기적으로 발행해야 한다. 발행이 멈춘
  것과 사람이 없는 것을 mission_manager가 구별해야 하기 때문이다.
- `trackId`(int, 0 이상)와 `confidence`(0~1)는 필수다. 추적 ID가 없는 탐지는
  아직 후보가 아니다.
- `encounterId`는 만들지 않는다(명세 26.1, Mission Manager 단일 권한).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def format_utc(moment: datetime) -> str:
    """person-candidates.schema.json의 observedAt pattern에 맞춘다."""
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def person_candidate(person: Any) -> dict[str, Any] | None:
    """PersonObservation 하나를 계약의 후보 항목으로 바꾼다.

    스키마가 trackId(int, 0 이상)를 요구하므로 추적 ID가 없는 탐지는
    None을 돌려 후보에서 제외한다.
    """
    det = person.detection
    if det.track_id is None or int(det.track_id) < 0:
        return None

    x1, y1, x2, y2 = det.bbox_xyxy
    width = round(float(x2 - x1), 2)
    height = round(float(y2 - y1), 2)
    box: dict[str, Any] | None = {
        "x": round(float(x1), 2),
        "y": round(float(y1), 2),
        "width": width,
        "height": height,
    }
    if width <= 0 or height <= 0:
        # 스키마가 width/height에 exclusiveMinimum 0을 요구한다.
        # 퇴화한 박스는 null로 보낸다(box는 nullable).
        box = None

    return {
        "trackId": int(det.track_id),
        "confidence": min(max(round(float(det.confidence), 4), 0.0), 1.0),
        "box": box,
        # 지도 좌표(25.3)는 LiDAR·TF 결합(human_localizer) 연동 후 채운다.
        "position": None,
    }


def confirmed_candidates(
    persons: list[Any], confirm_seconds: float
) -> list[dict[str, Any]]:
    """25.2 확정 기준(약 1초 안정 관측)을 넘긴 사람만 후보로 만든다."""
    result = []
    for person in persons:
        if person.seen_sec < confirm_seconds:
            continue
        candidate = person_candidate(person)
        if candidate is not None:
            result.append(candidate)
    return result


def candidates_body(
    candidates: list[dict[str, Any]],
    observed_at: str,
    frame_id: str | None = None,
) -> dict[str, Any]:
    """토픽에 실을 최상위 본문. 후보가 없어도 candidates는 빈 배열로 존재한다."""
    return {
        "observedAt": observed_at,
        "candidates": candidates,
        "frameId": frame_id,
    }

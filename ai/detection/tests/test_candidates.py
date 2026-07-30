"""`/perception/person_candidates` 본문 생성 검증 (S15P11A301-133 계약).

ROS 없이 계약 형식(common/schemas/person-candidates.schema.json)의 필수
규칙을 결정적으로 확인한다: trackId 필수, 빈 배열 허용, observedAt 패턴,
퇴화 박스의 null 처리.

실행:
    python -m pytest tests -q
    python tests/test_candidates.py     (pytest 없이도 동작)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.candidates import (  # noqa: E402
    candidates_body,
    confirmed_candidates,
    format_utc,
    person_candidate,
)
from src.schemas import Detection  # noqa: E402

# person-candidates.schema.json의 observedAt pattern과 동일하다.
OBSERVED_AT_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$"
)


@dataclass
class _Person:
    """PersonObservation에서 후보 변환에 쓰는 필드만 흉내 낸다."""

    detection: Detection
    seen_sec: float


def _person(
    track_id: int | None,
    *,
    confidence: float = 0.9,
    bbox: tuple[float, float, float, float] = (10.0, 20.0, 110.0, 220.0),
    seen_sec: float = 2.0,
) -> _Person:
    return _Person(
        detection=Detection(
            class_id=0,
            class_name="person",
            confidence=confidence,
            bbox_xyxy=bbox,
            track_id=track_id,
        ),
        seen_sec=seen_sec,
    )


def test_observed_at_matches_schema_pattern() -> None:
    stamp = format_utc(datetime(2026, 7, 30, 1, 2, 3, 456789, tzinfo=timezone.utc))
    assert OBSERVED_AT_PATTERN.match(stamp), stamp


def test_candidate_has_required_fields() -> None:
    candidate = person_candidate(_person(7, confidence=0.87))
    assert candidate is not None
    assert candidate["trackId"] == 7
    assert 0.0 <= candidate["confidence"] <= 1.0
    box = candidate["box"]
    assert box == {"x": 10.0, "y": 20.0, "width": 100.0, "height": 200.0}
    assert candidate["position"] is None


def test_confidence_is_clamped_to_schema_range() -> None:
    # 반올림·부동소수 오차로 1을 넘는 값이 나가면 스키마 위반이다.
    candidate = person_candidate(_person(1, confidence=1.00004))
    assert candidate is not None
    assert candidate["confidence"] == 1.0


def test_trackless_detection_is_not_a_candidate() -> None:
    # 스키마가 trackId(int, 0 이상)를 요구한다. 추적 전 탐지는 후보가 아니다.
    assert person_candidate(_person(None)) is None


def test_degenerate_box_becomes_null() -> None:
    # width/height는 exclusiveMinimum 0이다. 퇴화 박스는 null로 보낸다.
    candidate = person_candidate(_person(3, bbox=(50.0, 60.0, 50.0, 160.0)))
    assert candidate is not None
    assert candidate["box"] is None


def test_confirm_seconds_filters_unstable_tracks() -> None:
    persons = [
        _person(1, seen_sec=1.5),
        _person(2, seen_sec=0.4),  # 아직 25.2 확정 기준 미달
        _person(None, seen_sec=3.0),  # 추적 ID 없음
    ]
    candidates = confirmed_candidates(persons, confirm_seconds=1.0)
    assert [c["trackId"] for c in candidates] == [1]


def test_body_keeps_empty_candidates_array() -> None:
    # 후보가 없어도 candidates 키는 빈 배열로 존재해야 한다(계약).
    body = candidates_body([], format_utc(datetime.now(timezone.utc)))
    assert body["candidates"] == []
    assert OBSERVED_AT_PATTERN.match(body["observedAt"])
    assert body["frameId"] is None


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"{name}: OK")

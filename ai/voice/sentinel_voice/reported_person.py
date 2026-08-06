"""COUNT 발화에서 추가 요구조자 제보를 안전하게 정규화한다.

위치는 피해자가 말한 단서일 뿐 좌표나 주행 목표가 아니다. 자유형 문자열은 반드시
원문에 실제로 등장한 조각만 허용하고, 원문과 확인 상태를 함께 보존한다.
"""

from __future__ import annotations

import re
from typing import Any


MAX_REPORTS = 5
RESPONSE_STATUSES = frozenset({"UNKNOWN", "RESPONSIVE", "UNRESPONSIVE"})
CERTAINTY_STATUSES = frozenset({"ASSERTED", "TENTATIVE"})

_SPACE = re.compile(r"\s+")
_FLOOR = re.compile(r"(?P<basement>지하\s*)?(?P<number>\d+)\s*층")
_TENTATIVE = re.compile(r"(있을\s*지도|있는\s*것\s*같|아마|존재가\s*확실하지)")
_UNRESPONSIVE = re.compile(
    r"(대답|말|응답|의식).{0,8}(안\s*(해|하|함)|못\s*(해|하|함)|없)"
)
_RESPONSIVE = re.compile(r"(대답|응답|말).{0,8}(해|하|했|가능)")
_ABSENCE = re.compile(
    r"(아기|애기|아이|사람|인원|할머니|할아버지|어머니|아버지|엄마|아빠)"
    r".{0,10}(없|아니)"
)
_ALONE = re.compile(r"(저\s*)?(혼자|밖에\s*없|뿐이에|뿐이야)")
_OTHER_HINT = re.compile(
    r"(더\s*있|또\s*있|옆에|주변에|근처에|"
    r"저기|계단|복도|방|화장실|출입구|엘리베이터|현관|창문|"
    r"아기|애기|아이|사람|인원|할머니|할아버지|어머니|아버지|엄마|아빠|누가)"
)
_SUBJECT = re.compile(
    r"((?:우리\s*)?(?:아기|애기|아이|할머니|할아버지|어머니|아버지|엄마|아빠)|누가)"
)
_COUNT = re.compile(r"(한|하나|두|둘|세|셋|네|넷|다섯|\d+)\s*(?:명|사람)")
_LANDMARK = re.compile(
    r"(계단|복도|방|화장실|출입구|엘리베이터|현관|창문)"
    r"(?:\s*(?:옆|앞|뒤|왼쪽|오른쪽|안|안쪽|근처))?"
    r"|(?:바로\s*)?(옆|근처|주변)"
)

_KOREAN_NUMBERS = {
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
}


def _clean_optional(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = _SPACE.sub(" ", value).strip()
    return cleaned or None


def _squashed(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value)


def _source_span(value: Any, source_text: str) -> str | None:
    """모델이 원문에 없는 대상·장소를 만들면 버린다."""
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    if _squashed(cleaned) not in _squashed(source_text):
        return None
    return cleaned


def floor_from_text(value: str | None) -> int | None:
    match = _FLOOR.search(value or "")
    if match is None:
        return None
    floor = int(match.group("number"))
    return -floor if match.group("basement") else floor


def coerce_additional_person_reports(
    value: Any, source_text: str
) -> list[dict[str, Any]]:
    """GMS 출력을 공통 계약으로 제한하고 실제 STT 원문을 강제로 보존한다."""
    if not isinstance(value, list) or not source_text.strip():
        return []

    reports: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    candidates = value[:MAX_REPORTS]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        subject = _source_span(candidate.get("subjectText"), source_text)
        location = _source_span(candidate.get("locationText"), source_text)

        count = candidate.get("reportedCount")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            count = None

        # 대상·숫자를 생략해도 COUNT 답변에서 위치를 가리키며 "있다"고 하면
        # 사람 존재 제보다. 셋 모두 없을 때만 근거가 없다.
        if subject is None and count is None and location is None:
            continue

        response_status = candidate.get("responseStatus")
        if response_status not in RESPONSE_STATUSES:
            response_status = "UNKNOWN"

        certainty_status = candidate.get("certaintyStatus")
        if certainty_status not in CERTAINTY_STATUSES:
            certainty_status = (
                "TENTATIVE" if _TENTATIVE.search(source_text) else "ASSERTED"
            )

        # 층은 모델 숫자를 신뢰하지 않고, 원문에 존재하는 위치 표현에서 다시 계산한다.
        reported_floor = floor_from_text(location)
        if reported_floor is None and location is None and len(candidates) == 1:
            reported_floor = floor_from_text(source_text)

        key = (subject, count, location, reported_floor, response_status)
        if key in seen:
            continue
        seen.add(key)
        reports.append(
            {
                "subjectText": subject,
                "reportedCount": count,
                "countStatus": (
                    "EXACT"
                    if count is not None
                    else "PRESENCE_CONFIRMED_COUNT_UNKNOWN"
                ),
                "locationText": location,
                "reportedFloor": reported_floor,
                "groundingStatus": "UNGROUNDED",
                "responseStatus": response_status,
                "certaintyStatus": certainty_status,
                "rawUtterance": source_text.strip(),
                "verificationStatus": "UNVERIFIED",
                "operatorReviewRequired": True,
            }
        )
    return reports


def _keyword_candidate(text: str, subject: str | None) -> dict[str, Any]:
    count_match = _COUNT.search(text)
    count = None
    if count_match:
        token = count_match.group(1)
        count = int(token) if token.isdigit() else _KOREAN_NUMBERS[token]
    elif subject is not None and subject != "누가":
        count = 1

    floor_match = _FLOOR.search(text)
    landmark_match = _LANDMARK.search(text)
    location = None
    if floor_match and landmark_match:
        start = min(floor_match.start(), landmark_match.start())
        end = max(floor_match.end(), landmark_match.end())
        location = text[start:end].strip()
    elif floor_match:
        location = floor_match.group(0)
    elif landmark_match:
        location = landmark_match.group(0)

    response_status = "UNKNOWN"
    if _UNRESPONSIVE.search(text):
        response_status = "UNRESPONSIVE"
    elif _RESPONSIVE.search(text):
        response_status = "RESPONSIVE"

    return {
        "subjectText": subject,
        "reportedCount": count,
        "locationText": location,
        "responseStatus": response_status,
        "certaintyStatus": (
            "TENTATIVE" if _TENTATIVE.search(text) else "ASSERTED"
        ),
    }


def keyword_additional_person_reports(text: str) -> list[dict[str, Any]]:
    """GMS 장애 시 명시적인 추가 인원·위치 표현만 보존하는 축소 폴백."""
    normalized = (text or "").strip()
    if not normalized or _ALONE.search(normalized) or _ABSENCE.search(normalized):
        return []
    if not _OTHER_HINT.search(normalized):
        return []

    subject_matches = list(_SUBJECT.finditer(normalized))
    if len(subject_matches) <= 1:
        subject = subject_matches[0].group(1) if subject_matches else None
        candidates = [_keyword_candidate(normalized, subject)]
    else:
        candidates = []
        for index, match in enumerate(subject_matches):
            end = (
                subject_matches[index + 1].start()
                if index + 1 < len(subject_matches)
                else len(normalized)
            )
            segment = normalized[match.start() : end]
            candidates.append(_keyword_candidate(segment, match.group(1)))

    return coerce_additional_person_reports(candidates, normalized)

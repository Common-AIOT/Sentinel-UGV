"""음성 세션 결과를 ROS 2 통합 계약으로 변환한다 (S15P11A301-159).

이 모듈은 ROS 2와 오디오 라이브러리를 import하지 않는다. 보고 본문과 Mission
Signal을 만드는 순수 변환을 분리해 CI에서 공통 JSON Schema로 검증할 수 있게 한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .conversation import SessionResult
from .encounter import EncounterContext
from .safety import coerce_report, risk_assessment


def utc_now_iso() -> str:
    """공통 계약이 요구하는 UTC ``Z`` 시각을 만든다."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def build_interaction_report(
    context: EncounterContext,
    result: SessionResult,
    *,
    started_at: str,
    ended_at: str,
    used_fallback: bool = False,
    interaction_id: str | None = None,
) -> dict[str, Any]:
    """Encounter 문맥과 33-6 세션 결과를 ``INTERACTION_REPORT`` 본문으로 만든다."""

    if context.mission_id is None:
        raise ValueError("missionId 없는 encounter는 관제 보고를 만들 수 없습니다")

    report = coerce_report(dict(result.fields))
    risk = risk_assessment(report)
    report["operatorReviewRequired"] = bool(
        report["operatorReviewRequired"] or risk["operatorReviewRequired"]
    )
    return {
        "interactionId": interaction_id or str(uuid.uuid4()),
        "encounterId": context.encounter_id,
        "missionId": context.mission_id,
        "visionPersonCount": context.person_count,
        "startedAt": started_at,
        "endedAt": ended_at,
        "sessionReport": report,
        "riskAssessment": risk,
        "usedFallback": bool(used_fallback),
    }


def dialogue_ended_signal(
    context: EncounterContext,
    *,
    sent_at: str | None = None,
    termination_reason: str | None = None,
) -> dict[str, Any]:
    """Mission Manager에 대화 종료를 알리는 ``/mission/signal`` 본문."""

    return {
        "signal": "DIALOGUE_ENDED",
        "sentAt": sent_at or utc_now_iso(),
        "source": "VOICE",
        "encounterId": context.encounter_id,
        "missionId": context.mission_id,
        "commandId": None,
        "detail": f"음성 세션 종료: {termination_reason or 'UNKNOWN'}",
    }


def mission_abort_state(payload: Any) -> str | None:
    """Mission status에서 음성 세션 중단 종류를 결정한다."""

    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if state in {"ESTOP", "ERROR"}:
        return "SAFETY"
    if state in {"MANUAL", "PAUSED"}:
        return "MANUAL"
    return None

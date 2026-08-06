import json
from pathlib import Path

import pytest

from sentinel_voice.conversation import SessionResult, SessionState
from sentinel_voice.encounter import EncounterContext, EncounterPhase
from sentinel_voice.integration import (
    build_interaction_report,
    dialogue_ended_signal,
    mission_abort_state,
)


MISSION_ID = "1cb5350f-187f-4478-b95e-bb513c47e706"
ENCOUNTER_ID = "32f6f147-dacc-4979-a9a2-7aab8fed689c"


def _context(mission_id=MISSION_ID):
    return EncounterContext(
        encounter_id=ENCOUNTER_ID,
        phase=EncounterPhase.APPROACHED,
        detected_at="2026-07-30T09:16:00.000Z",
        person_count=3,
        mission_id=mission_id,
        pose={"x": 12.4, "y": 7.8, "yaw": 1.57, "mapId": "floor-1"},
    )


def test_interaction_report_passes_common_schema():
    jsonschema = pytest.importorskip("jsonschema")
    result = SessionResult(
        state=SessionState.COMPLETED,
        fields={
            "responseScope": "GROUP",
            "anyResponseDetected": True,
            "reportedResponsiveCount": 2,
            "reportedCountStatus": "SELF_REPORTED_GROUP_COUNT",
            "countConfidence": None,
            "mobilityStatus": "NO",
            "urgentConditionReported": "YES",
            "operatorReviewRequired": True,
            "terminationReason": "NORMAL",
        },
        termination_reason="NORMAL",
        additional_person_reports=[
            {
                "subjectText": "우리 아기",
                "reportedCount": 1,
                "countStatus": "EXACT",
                "locationText": "2층",
                "reportedFloor": 2,
                "groundingStatus": "UNGROUNDED",
                "responseStatus": "UNKNOWN",
                "certaintyStatus": "ASSERTED",
                "rawUtterance": "2층에 우리 아기가 있어요",
                "verificationStatus": "UNVERIFIED",
                "operatorReviewRequired": True,
            }
        ],
    )
    report = build_interaction_report(
        _context(),
        result,
        started_at="2026-07-30T09:16:12.003Z",
        ended_at="2026-07-30T09:17:30.994Z",
        interaction_id="74ebbf7d-5726-4c4a-95b2-b899afe8543a",
    )

    schema_path = (
        Path(__file__).resolve().parents[3]
        / "common"
        / "schemas"
        / "interaction-report.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(report))
    assert errors == []
    assert report["riskAssessment"]["riskLevel"] == "IMMEDIATE"
    assert report["visionPersonCount"] == 3
    assert report["encounterPose"]["mapId"] == "floor-1"
    assert report["additionalPersonReports"][0]["reportedFloor"] == 2
    assert report["additionalPersonReports"][0]["responseStatus"] == "UNKNOWN"


def test_report_requires_mission_id():
    with pytest.raises(ValueError, match="missionId"):
        build_interaction_report(
            _context(None),
            SessionResult(),
            started_at="2026-07-30T09:16:12.003Z",
            ended_at="2026-07-30T09:17:30.994Z",
        )


def test_dialogue_signal_preserves_encounter_context():
    signal = dialogue_ended_signal(
        _context(),
        sent_at="2026-07-30T09:17:31.000Z",
        termination_reason="NORMAL",
    )
    assert signal == {
        "signal": "DIALOGUE_ENDED",
        "sentAt": "2026-07-30T09:17:31.000Z",
        "source": "VOICE",
        "encounterId": ENCOUNTER_ID,
        "missionId": MISSION_ID,
        "commandId": None,
        "detail": "음성 세션 종료: NORMAL",
    }


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("ESTOP", "SAFETY"),
        ("ERROR", "SAFETY"),
        ("MANUAL", "MANUAL"),
        ("PAUSED", "MANUAL"),
        ("INTERACTING", None),
    ],
)
def test_mission_abort_state(state, expected):
    assert mission_abort_state({"state": state}) == expected

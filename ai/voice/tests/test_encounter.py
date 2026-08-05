import json
import unittest

from sentinel_voice.encounter import (
    EncounterContractError,
    EncounterPhase,
    EncounterSessionCoordinator,
    EncounterSessionState,
    execute_encounter_outcome,
    parse_encounter_message,
)


EID = "c81f6d20-5a47-4e93-b2d8-1f70e4a95c33"
OTHER_EID = "a81f6d20-5a47-4e93-b2d8-1f70e4a95c33"
MID = "4a43f45c-779f-4df5-ac04-1695724829a4"


def body(phase="CONFIRMED", encounter_id=EID, **overrides):
    value = {
        "encounterId": encounter_id,
        "phase": phase,
        "detectedAt": "2026-07-28T04:30:11.180Z",
        "personCount": 3,
        "trackIds": [12, 13, 15],
        "confidence": 0.87,
        "pose": {"x": 4.31, "y": 1.82, "yaw": 0.74, "mapId": "map-1"},
        "missionId": MID,
    }
    value.update(overrides)
    return value


def event(phase, encounter_id=EID, **overrides):
    return parse_encounter_message(body(phase, encounter_id, **overrides))


class EncounterContractTest(unittest.TestCase):
    def test_accepts_body_and_envelope(self):
        direct = parse_encounter_message(json.dumps(body()))
        wrapped = parse_encounter_message({"messageType": "ENCOUNTER_CONFIRMED", "data": body()})

        self.assertEqual(direct.encounter_id, EID)
        self.assertEqual(wrapped.phase, EncounterPhase.CONFIRMED)
        self.assertEqual(wrapped.track_ids, (12, 13, 15))
        self.assertEqual(
            wrapped.report_context(),
            {"encounterId": EID, "missionId": MID, "visionPersonCount": 3},
        )

    def test_rejects_invalid_json_and_missing_required_field(self):
        with self.assertRaises(EncounterContractError):
            parse_encounter_message("{")
        invalid = body()
        del invalid["personCount"]
        with self.assertRaises(EncounterContractError):
            parse_encounter_message(invalid)

    def test_rejects_unknown_phase_extra_field_and_bad_types(self):
        for invalid in (
            body(phase="TALKING"),
            body(unexpected=True),
            body(personCount=True),
            body(confidence=1.1),
            body(trackIds=[1, "2"]),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(EncounterContractError):
                    parse_encounter_message(invalid)

    def test_rejects_noncanonical_ids_and_non_utc_time(self):
        with self.assertRaises(EncounterContractError):
            parse_encounter_message(body(encounterId="REPORT-1"))
        with self.assertRaises(EncounterContractError):
            parse_encounter_message(body(detectedAt="2026-07-28 13:30:11"))


class EncounterSessionCoordinatorTest(unittest.TestCase):
    def test_normal_flow_starts_only_after_approached(self):
        coordinator = EncounterSessionCoordinator()

        confirmed = coordinator.handle(event("CONFIRMED"))
        self.assertEqual(confirmed.state, EncounterSessionState.WAITING_APPROACH)
        self.assertFalse(confirmed.start_conversation)

        approached = coordinator.handle(event("APPROACHED", personCount=0))
        self.assertEqual(approached.state, EncounterSessionState.INTERACTION_ACTIVE)
        self.assertTrue(approached.start_conversation)

        finished = coordinator.conversation_finished()
        self.assertEqual(finished.state, EncounterSessionState.WAITING_ENDED)
        self.assertTrue(finished.handoff_report)
        self.assertTrue(finished.notify_dialogue_completed)
        self.assertEqual(finished.report_context["encounterId"], EID)
        self.assertEqual(finished.report_context["visionPersonCount"], 3)

        ended = coordinator.handle(event("ENDED", personCount=0))
        self.assertEqual(ended.state, EncounterSessionState.COMPLETED)

    def test_approached_before_confirmed_is_ignored(self):
        coordinator = EncounterSessionCoordinator()
        outcome = coordinator.handle(event("APPROACHED"))
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.state, EncounterSessionState.IDLE)

    def test_duplicate_events_never_start_second_session(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))

        duplicate_confirmed = coordinator.handle(event("CONFIRMED"))
        first_approached = coordinator.handle(event("APPROACHED"))
        duplicate_approached = coordinator.handle(event("APPROACHED"))

        self.assertFalse(duplicate_confirmed.accepted)
        self.assertTrue(first_approached.start_conversation)
        self.assertFalse(duplicate_approached.accepted)
        self.assertFalse(duplicate_approached.start_conversation)

    def test_other_encounter_cannot_replace_active_one(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        coordinator.handle(event("APPROACHED"))

        conflict = coordinator.handle(event("CONFIRMED", OTHER_EID))
        self.assertFalse(conflict.accepted)
        self.assertEqual(coordinator.context.encounter_id, EID)
        self.assertEqual(
            coordinator.state,
            EncounterSessionState.INTERACTION_ACTIVE,
        )

    def test_lost_aborts_active_conversation_without_report_or_resume(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        coordinator.handle(event("APPROACHED"))

        lost = coordinator.handle(event("LOST"))
        self.assertTrue(lost.accepted)
        self.assertTrue(lost.abort_conversation)
        self.assertFalse(lost.handoff_report)
        self.assertEqual(lost.state, EncounterSessionState.LOST)

    def test_manual_and_safety_abort_never_handoff_report(self):
        for abort_method in ("abort_manual", "abort_safety"):
            coordinator = EncounterSessionCoordinator()
            coordinator.handle(event("CONFIRMED"))
            coordinator.handle(event("APPROACHED"))

            outcome = getattr(coordinator, abort_method)()
            self.assertTrue(outcome.abort_conversation)
            self.assertFalse(outcome.handoff_report)
            self.assertFalse(outcome.notify_dialogue_completed)

    def test_ended_before_conversation_completion_is_ignored(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        coordinator.handle(event("APPROACHED"))

        ended = coordinator.handle(event("ENDED"))
        self.assertFalse(ended.accepted)
        self.assertEqual(
            coordinator.state,
            EncounterSessionState.INTERACTION_ACTIVE,
        )

    def test_completed_encounter_cannot_restart(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        coordinator.handle(event("APPROACHED"))
        coordinator.conversation_finished()
        coordinator.handle(event("ENDED"))

        duplicate = coordinator.handle(event("CONFIRMED"))
        redetected = coordinator.handle(event("REDETECTED"))
        self.assertFalse(duplicate.accepted)
        self.assertFalse(redetected.accepted)
        self.assertFalse(duplicate.start_conversation)
        self.assertFalse(redetected.start_conversation)

        next_encounter = coordinator.handle(event("CONFIRMED", OTHER_EID))
        self.assertTrue(next_encounter.accepted)
        self.assertEqual(
            next_encounter.state,
            EncounterSessionState.WAITING_APPROACH,
        )

    def test_executor_calls_only_requested_boundaries(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        start = coordinator.handle(event("APPROACHED"))
        calls = []

        start_execution = execute_encounter_outcome(
            start,
            start_conversation=lambda: calls.append("start"),
            abort_conversation=lambda: calls.append("abort"),
            handoff_report=lambda context: calls.append(("report", context)),
            notify_dialogue_completed=lambda context: calls.append(
                ("notify", context)
            ),
        )
        self.assertTrue(start_execution.conversation_started)
        self.assertEqual(calls, ["start"])

        finished = coordinator.conversation_finished()
        finish_execution = execute_encounter_outcome(
            finished,
            start_conversation=lambda: calls.append("unexpected-start"),
            abort_conversation=lambda: calls.append("unexpected-abort"),
            handoff_report=lambda context: calls.append(("report", context)),
            notify_dialogue_completed=lambda context: calls.append(
                ("notify", context)
            ),
        )
        self.assertTrue(finish_execution.report_handed_off)
        self.assertTrue(finish_execution.dialogue_completion_notified)
        self.assertEqual(calls[1][0], "report")
        self.assertEqual(calls[2][0], "notify")
        self.assertEqual(calls[1][1]["encounterId"], EID)

    def test_executor_contains_adapter_errors(self):
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        start = coordinator.handle(event("APPROACHED"))

        def broken():
            raise RuntimeError("ROS details must not leak")

        execution = execute_encounter_outcome(
            start,
            start_conversation=broken,
            abort_conversation=lambda: None,
            handoff_report=lambda _context: None,
            notify_dialogue_completed=lambda _context: None,
        )
        self.assertFalse(execution.conversation_started)
        self.assertEqual(execution.errors, ("start_conversation:RuntimeError",))


if __name__ == "__main__":
    unittest.main()

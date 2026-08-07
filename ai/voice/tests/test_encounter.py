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
        # 유실이 대화를 중단시키고 보고·재개를 만들지 않는다는 규범은 그대로다.
        # 바뀐 것은 **시점**뿐이다 — 즉시가 아니라 재감지 유예가 만료된 뒤다
        # (S15P11A301-332). 즉시 중단하면 0.18초 끊김에 대화가 죽었고, 실측에서
        # 답을 받아놓고 버렸다.
        coordinator = EncounterSessionCoordinator()
        coordinator.handle(event("CONFIRMED"))
        coordinator.handle(event("APPROACHED"))

        lost = coordinator.handle(event("LOST"))
        self.assertTrue(lost.accepted)
        self.assertFalse(lost.abort_conversation)
        self.assertEqual(lost.state, EncounterSessionState.LOST_GRACE)

        expired = coordinator.lost_grace_closed()
        self.assertTrue(expired.accepted)
        self.assertTrue(expired.abort_conversation)
        self.assertFalse(expired.handoff_report)
        self.assertEqual(expired.state, EncounterSessionState.LOST)

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


class LostGraceTest(unittest.TestCase):
    """순간 유실로 대화를 버리지 않는다 (S15P11A301-332).

    실측에서 유실 구간이 0.179초였고, mission_manager 는 그것을 사후 3초 창의
    재감지로 회복했는데 voice 는 종결로 처리했다. 답까지 받아놓고 버렸다.
    """

    def _active(self):
        c = EncounterSessionCoordinator()
        c.handle(parse_encounter_message(body("CONFIRMED")))
        started = c.handle(parse_encounter_message(body("APPROACHED")))
        self.assertTrue(started.start_conversation)
        self.assertIs(c.state, EncounterSessionState.INTERACTION_ACTIVE)
        return c

    def test_대화중_유실은_종결하지_않고_유예에_들어간다(self):
        c = self._active()
        out = c.handle(parse_encounter_message(body("LOST")))
        self.assertTrue(out.accepted)
        # 여기서 중단하면 0.18초 끊김에 대화가 죽는다.
        self.assertFalse(out.abort_conversation)
        self.assertIs(c.state, EncounterSessionState.LOST_GRACE)

    def test_유예중_재감지는_대화를_잇는다(self):
        c = self._active()
        c.handle(parse_encounter_message(body("LOST")))
        out = c.handle(parse_encounter_message(body("REDETECTED")))
        self.assertTrue(out.accepted)
        self.assertFalse(out.abort_conversation)
        self.assertIs(c.state, EncounterSessionState.INTERACTION_ACTIVE)

    def test_유예중_재접근도_대화를_잇는다(self):
        c = self._active()
        c.handle(parse_encounter_message(body("LOST")))
        out = c.handle(parse_encounter_message(body("APPROACHED")))
        self.assertTrue(out.accepted)
        self.assertIs(c.state, EncounterSessionState.INTERACTION_ACTIVE)

    def test_유예_만료는_그때_종결한다(self):
        c = self._active()
        c.handle(parse_encounter_message(body("LOST")))
        out = c.lost_grace_closed()
        self.assertTrue(out.accepted)
        self.assertTrue(out.abort_conversation)
        self.assertIs(c.state, EncounterSessionState.LOST)

    def test_유예중이_아니면_만료_통지를_무시한다(self):
        c = self._active()
        out = c.lost_grace_closed()
        self.assertFalse(out.accepted)
        self.assertIs(c.state, EncounterSessionState.INTERACTION_ACTIVE)

    def test_대화중이_아닌_유실은_종전대로_즉시_종결한다(self):
        # 접근 대기 중 유실은 유예해서 얻을 것이 없다. 다시 CONFIRMED 부터 온다.
        c = EncounterSessionCoordinator()
        c.handle(parse_encounter_message(body("CONFIRMED")))
        out = c.handle(parse_encounter_message(body("LOST")))
        self.assertTrue(out.accepted)
        self.assertIs(c.state, EncounterSessionState.LOST)


class QueuedSessionTest(unittest.TestCase):
    """배수 완료 뒤 큐에 담아 둔 세션을 시작해도 되는지 (S15P11A301-334).

    중단은 비동기다 — `_abort_state`만 세워지고 작업 스레드는 진행 중인 녹음·STT
    주기를 마친 뒤에야 확인하므로 배수에 4~10초가 걸린다. 그 사이에 도착한 문맥을
    버려서 encounter 하나가 45초 동안 한마디도 하지 않았다.
    """

    def _active(self):
        c = EncounterSessionCoordinator()
        c.handle(parse_encounter_message(body("CONFIRMED")))
        c.handle(parse_encounter_message(body("APPROACHED")))
        return c

    def test_진행_중인_encounter는_시작할_수_있다(self):
        c = self._active()
        self.assertTrue(c.startable(EID))

    def test_다른_encounter는_시작하지_않는다(self):
        c = self._active()
        self.assertFalse(c.startable(OTHER_EID))

    def test_배수_중_종료된_encounter는_시작하지_않는다(self):
        # 배수가 10초 걸리는 동안 대화가 완료되고 ENDED 까지 온 경우.
        c = self._active()
        c.conversation_finished(termination_reason="COMPLETED")
        c.handle(parse_encounter_message(body("ENDED")))
        self.assertFalse(c.startable(EID))

    def test_유예_만료로_종결된_encounter는_시작하지_않는다(self):
        c = self._active()
        c.handle(parse_encounter_message(body("LOST")))
        c.lost_grace_closed()
        self.assertFalse(c.startable(EID))

    def test_유예_중에는_아직_시작하지_않는다(self):
        # 유예는 대화가 살아 있는 상태다. 새로 띄우면 두 세션이 겹친다.
        c = self._active()
        c.handle(parse_encounter_message(body("LOST")))
        self.assertFalse(c.startable(EID))

    def test_접근_대기_중에는_시작하지_않는다(self):
        c = EncounterSessionCoordinator()
        c.handle(parse_encounter_message(body("CONFIRMED")))
        self.assertFalse(c.startable(EID))

    def test_아무것도_진행하지_않으면_시작하지_않는다(self):
        self.assertFalse(EncounterSessionCoordinator().startable(EID))

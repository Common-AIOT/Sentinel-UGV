import unittest

from sentinel_voice.guide_audio import GuideCode
from sentinel_voice.report_lifecycle import (
    LifecycleEvent,
    LifecycleEventType,
    LifecycleState,
    ReportLifecycle,
    execute_outcome,
)


REPORT_ID = "report-001"


def event(event_type, report_id=REPORT_ID):
    return LifecycleEvent(event_type, report_id)


class ReportLifecycleTest(unittest.TestCase):
    def test_ack_then_resume_plays_combined_closing_once(self):
        machine = ReportLifecycle(REPORT_ID)

        ack = machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))
        self.assertEqual(ack.state, LifecycleState.WAITING_RESUME_DECISION)
        self.assertIsNone(ack.guide_code)

        resume = machine.handle(event(LifecycleEventType.RESUME_APPROVED))
        self.assertEqual(resume.state, LifecycleState.READY_TO_RESUME)
        self.assertEqual(
            resume.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE
        )
        self.assertTrue(resume.request_exploration_resume)

        duplicate = machine.handle(event(LifecycleEventType.RESUME_APPROVED))
        self.assertFalse(duplicate.accepted)
        self.assertIsNone(duplicate.guide_code)
        self.assertFalse(duplicate.request_exploration_resume)

    def test_resume_before_ack_is_safe_and_order_independent(self):
        machine = ReportLifecycle(REPORT_ID)
        early_resume = machine.handle(event(LifecycleEventType.RESUME_APPROVED))
        self.assertTrue(early_resume.accepted)
        self.assertIsNone(early_resume.guide_code)
        self.assertFalse(early_resume.request_exploration_resume)

        ack = machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))
        self.assertEqual(ack.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE)
        self.assertTrue(ack.request_exploration_resume)

    def test_duplicate_ack_before_resume_does_not_replay_or_change_state(self):
        machine = ReportLifecycle(REPORT_ID)
        machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))

        duplicate = machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))
        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.state, LifecycleState.WAITING_RESUME_DECISION)
        self.assertIsNone(duplicate.guide_code)
        self.assertFalse(duplicate.request_exploration_resume)

    def test_failed_ack_after_success_is_ignored(self):
        machine = ReportLifecycle(REPORT_ID)
        machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))

        conflict = machine.handle(event(LifecycleEventType.REPORT_ACK_FAILED))
        self.assertFalse(conflict.accepted)
        self.assertEqual(conflict.state, LifecycleState.WAITING_RESUME_DECISION)
        self.assertIsNone(conflict.guide_code)

    def test_ack_without_resume_times_out_to_success_and_stay(self):
        machine = ReportLifecycle(REPORT_ID)
        machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))

        timeout = machine.handle(event(LifecycleEventType.CLOSING_TIMEOUT))
        self.assertEqual(timeout.state, LifecycleState.REPORT_CONFIRMED_STAY)
        self.assertEqual(timeout.guide_code, GuideCode.REPORT_SUCCEEDED)
        self.assertFalse(timeout.request_exploration_resume)

    def test_resume_rejection_never_requests_motion(self):
        machine = ReportLifecycle(REPORT_ID)
        machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))

        rejected = machine.handle(event(LifecycleEventType.RESUME_REJECTED))
        self.assertEqual(rejected.guide_code, GuideCode.REPORT_SUCCEEDED)
        self.assertFalse(rejected.request_exploration_resume)

    def test_ack_failure_uses_network_wait(self):
        machine = ReportLifecycle(REPORT_ID)
        failed = machine.handle(event(LifecycleEventType.REPORT_ACK_FAILED))
        self.assertEqual(failed.state, LifecycleState.DELIVERY_FAILED)
        self.assertEqual(failed.guide_code, GuideCode.NETWORK_WAIT)
        self.assertFalse(failed.request_exploration_resume)

    def test_ack_timeout_keeps_report_pending(self):
        machine = ReportLifecycle(REPORT_ID)
        timeout = machine.handle(event(LifecycleEventType.CLOSING_TIMEOUT))
        self.assertEqual(timeout.state, LifecycleState.REPORT_PENDING)
        self.assertEqual(timeout.guide_code, GuideCode.REPORT_PENDING)

    def test_mismatched_report_id_is_ignored(self):
        machine = ReportLifecycle(REPORT_ID)
        outcome = machine.handle(
            event(LifecycleEventType.REPORT_ACK_SUCCEEDED, "report-999")
        )
        self.assertFalse(outcome.accepted)
        self.assertEqual(machine.state, LifecycleState.WAITING_REPORT_ACK)

    def test_empty_report_id_is_rejected(self):
        with self.assertRaises(ValueError):
            ReportLifecycle("")

    def test_executor_requests_mission_manager_only_after_both_approvals(self):
        class Player:
            def __init__(self):
                self.calls = []

            def play(self, code, **kwargs):
                self.calls.append((code, kwargs))
                return "played"

        player = Player()
        resume_calls = []
        machine = ReportLifecycle(REPORT_ID)

        waiting = machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))
        waiting_execution = execute_outcome(
            waiting,
            guide_player=player,
            request_mission_resume=lambda: resume_calls.append(True) or True,
        )
        self.assertFalse(waiting_execution.resume_request_sent)
        self.assertEqual(player.calls, [])
        self.assertEqual(resume_calls, [])

        ready = machine.handle(event(LifecycleEventType.RESUME_APPROVED))
        ready_execution = execute_outcome(
            ready,
            guide_player=player,
            request_mission_resume=lambda: resume_calls.append(True) or True,
        )
        self.assertTrue(ready_execution.resume_request_sent)
        self.assertTrue(ready_execution.resume_request_accepted)
        self.assertEqual(resume_calls, [True])
        self.assertEqual(
            player.calls,
            [
                (
                    GuideCode.REPORT_SUCCEEDED_DEPARTURE,
                    {
                        "report_succeeded": True,
                        "exploration_resume_approved": True,
                    },
                )
            ],
        )

    def test_executor_contains_mission_manager_adapter_error(self):
        class Player:
            def play(self, *_args, **_kwargs):
                return "played"

        machine = ReportLifecycle(REPORT_ID)
        machine.handle(event(LifecycleEventType.RESUME_APPROVED))
        ready = machine.handle(event(LifecycleEventType.REPORT_ACK_SUCCEEDED))

        def broken_resume():
            raise RuntimeError("motor details must not leak")

        execution = execute_outcome(
            ready,
            guide_player=Player(),
            request_mission_resume=broken_resume,
        )
        self.assertTrue(execution.resume_request_sent)
        self.assertFalse(execution.resume_request_accepted)
        self.assertEqual(execution.resume_request_error, "RuntimeError")


if __name__ == "__main__":
    unittest.main()

import unittest

from sentinel_voice.guide_audio import GuideCode
from sentinel_voice.report_delivery import DeliveryState, queue_report


class ReportDeliveryTest(unittest.TestCase):
    """종료 안내는 발신 상태와 무관하게 단일 문구다(146 v2, 실패 없음 가정).

    상태(PENDING/QUEUED/FAILED)는 그대로 구분해 로그·세션 기록에 남긴다 —
    문구만 통일했다. 실패 시 완료 안내가 나가는 잔여 위험은 문서 §11에 기록.
    """

    def test_missing_adapter_stays_pending_with_departure_guide(self):
        result = queue_report({"sessionId": "session-1"})
        self.assertEqual(result.state, DeliveryState.PENDING)
        self.assertEqual(
            result.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE
        )

    def test_queue_acceptance_uses_departure_guide(self):
        result = queue_report({"sessionId": "session-1"}, lambda _: True)
        self.assertEqual(result.state, DeliveryState.QUEUED)
        self.assertEqual(
            result.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE
        )

    def test_queue_failure_keeps_failed_state_for_the_record(self):
        result = queue_report({"sessionId": "session-1"}, lambda _: False)
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertEqual(
            result.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE
        )

    def test_adapter_exception_does_not_escape(self):
        def broken(_):
            raise OSError("offline")

        result = queue_report({"sessionId": "session-1"}, broken)
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertNotIn("offline", result.detail)


if __name__ == "__main__":
    unittest.main()

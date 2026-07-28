import unittest

from sentinel_voice.guide_audio import GuideCode
from sentinel_voice.report_delivery import DeliveryState, queue_report


class ReportDeliveryTest(unittest.TestCase):
    def test_missing_adapter_stays_pending(self):
        result = queue_report({"sessionId": "session-1"})
        self.assertEqual(result.state, DeliveryState.PENDING)
        self.assertEqual(result.guide_code, GuideCode.REPORT_PENDING)

    def test_queue_acceptance_is_not_report_success(self):
        result = queue_report({"sessionId": "session-1"}, lambda _: True)
        self.assertEqual(result.state, DeliveryState.QUEUED)
        self.assertEqual(result.guide_code, GuideCode.REPORT_PENDING)

    def test_queue_failure_uses_network_wait(self):
        result = queue_report({"sessionId": "session-1"}, lambda _: False)
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertEqual(result.guide_code, GuideCode.NETWORK_WAIT)

    def test_adapter_exception_does_not_escape(self):
        def broken(_):
            raise OSError("offline")

        result = queue_report({"sessionId": "session-1"}, broken)
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertNotIn("offline", result.detail)


if __name__ == "__main__":
    unittest.main()

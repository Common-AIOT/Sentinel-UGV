import unittest
from unittest.mock import patch

from sentinel_voice.guide_audio import GuideCode
from sentinel_voice.session_gate import SessionGateState, check_session_gate


class SessionGateTest(unittest.TestCase):
    @patch("sentinel_voice.session_gate.config.GMS_KEY", "")
    def test_missing_key_is_configuration_failure(self):
        result = check_session_gate(lambda _: True)
        self.assertFalse(result.proceed)
        self.assertEqual(result.state, SessionGateState.GMS_MISCONFIGURED)
        self.assertEqual(result.guide_code, GuideCode.NETWORK_WAIT)

    @patch("sentinel_voice.session_gate.config.GMS_KEY", "configured")
    def test_unreachable_gms_blocks_stt_session(self):
        result = check_session_gate(lambda _: False)
        self.assertFalse(result.proceed)
        self.assertEqual(result.state, SessionGateState.GMS_UNAVAILABLE)
        self.assertTrue(result.operator_review_required)

    @patch("sentinel_voice.session_gate.config.GMS_KEY", "configured")
    def test_reachable_gms_allows_session(self):
        result = check_session_gate(lambda _: True)
        self.assertTrue(result.proceed)
        self.assertEqual(result.state, SessionGateState.READY)
        self.assertIsNone(result.guide_code)


if __name__ == "__main__":
    unittest.main()

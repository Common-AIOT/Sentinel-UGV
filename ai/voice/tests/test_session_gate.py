import unittest
from unittest.mock import patch

from sentinel_voice.session_gate import SessionGateState, check_session_gate


class SessionGateTest(unittest.TestCase):
    @patch("sentinel_voice.session_gate.config.GMS_KEY", "")
    def test_missing_key_is_configuration_failure(self):
        result = check_session_gate(lambda _: True)
        self.assertFalse(result.proceed)
        self.assertEqual(result.state, SessionGateState.GMS_MISCONFIGURED)
        # 차단 안내 문구는 146 v2에서 삭제됐다. 차단은 로그로만 남는다.
        self.assertIsNone(result.guide_code)

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

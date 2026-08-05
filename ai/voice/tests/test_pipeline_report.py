"""pipeline.report_session의 보고값 보정 규칙 검증.

pipeline 모듈은 sounddevice·torch·silero_vad를 import하므로, 해당 패키지가
없는 환경에서는 이 테스트를 건너뛴다. 세션 배선 자체는 test_session_runner에서
하드웨어 없이 검증한다.
"""

import unittest

from sentinel_voice.conversation import SessionResult, SessionState
from sentinel_voice.guide_audio import GuideCode
from sentinel_voice.safety import report_defaults, risk_assessment

pipeline = None
try:  # pragma: no cover - 환경 의존
    from sentinel_voice import pipeline as _pipeline

    pipeline = _pipeline
except Exception:  # noqa: BLE001
    pipeline = None


@unittest.skipIf(pipeline is None, "오디오·STT 런타임이 없는 환경")
class ReportSessionTest(unittest.TestCase):
    def setUp(self):
        self.queued = []
        self.spoken = []
        self._queue_report = pipeline.queue_report
        self._speak = pipeline.speak

        class Delivery:
            state = type("S", (), {"value": "PENDING"})()
            detail = "테스트"
            guide_code = GuideCode.REPORT_SUCCEEDED_DEPARTURE

        pipeline.queue_report = lambda info: (
            self.queued.append(info) or Delivery()
        )
        pipeline.speak = lambda text, **kwargs: self.spoken.append(text)

    def tearDown(self):
        pipeline.queue_report = self._queue_report
        pipeline.speak = self._speak

    def _session(self, **fields):
        result = SessionResult(state=SessionState.COMPLETED)
        result.fields = report_defaults()
        result.fields.update(fields)
        result.termination_reason = fields.get("terminationReason", "NORMAL")
        return result

    def test_operator_review_is_forced_by_safety_policy(self):
        """상태머신이 false로 두어도 안전 정책이 확인을 요구하면 true로 보고한다."""
        result = self._session(
            anyResponseDetected=True,
            urgentConditionReported="YES",
            mobilityStatus="NO",
            operatorReviewRequired=False,
            terminationReason="NORMAL",
        )
        info = pipeline.report_session(result)

        self.assertTrue(info["operatorReviewRequired"])
        self.assertTrue(risk_assessment(info)["operatorReviewRequired"])
        self.assertEqual(self.queued[-1]["operatorReviewRequired"], True)

    def test_report_keeps_observed_values(self):
        """보정은 확인 필요 여부만 건드리고 관찰값은 바꾸지 않는다."""
        result = self._session(
            anyResponseDetected=True,
            reportedResponsiveCount=2,
            mobilityStatus="NO",
            urgentConditionReported="NO",
            terminationReason="NORMAL",
        )
        info = pipeline.report_session(result)

        self.assertEqual(info["reportedResponsiveCount"], 2)
        self.assertEqual(info["mobilityStatus"], "NO")
        self.assertEqual(info["urgentConditionReported"], "NO")


if __name__ == "__main__":
    unittest.main()

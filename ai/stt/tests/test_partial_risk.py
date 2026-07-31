"""세션 미완료 시 수집한 위험도 결과를 보존한다 (S15P11A301-179).

`voice-risk-v1.0`은 `terminationReason`이 `NORMAL`이 아니면 즉시 `UNKNOWN`을
반환했다. 그래서 네 질문에 모두 답을 받고 마지막 안내만 남은 세션이 제한 시간을
1초 넘기면, 확인된 긴급 상태가 사라졌다.

`riskLevel`은 관제가 우선순위를 정렬하는 필드다. 이는 보수적 처리가 아니라
**알고 있던 정보를 버려서 늦어지는 것**이므로 v1.1에서 종료 사유를 게이트가 아닌
부가 정보로 바꿨다.
"""

import unittest
from unittest.mock import patch

from sentinel_voice.conversation import (
    SESSION_TIMEOUT_SECONDS,
    ConversationMachine,
    SessionResult,
)
from sentinel_voice.safety import (
    COMPLETE_TERMINATIONS,
    RISK_RULE_VERSION,
    report_defaults,
    risk_assessment,
)
from sentinel_voice.session_runner import SessionDependencies, VoiceSessionRunner


def _silent_dependencies():
    """예산 배선만 확인하는 테스트용. 상태머신을 대역으로 바꾸므로 호출되지 않는다."""
    return SessionDependencies(
        record=lambda seconds: None,
        has_speech=lambda wav: False,
        transcribe=lambda wav: ("", 1.0),
        extract=lambda text: None,
        player=None,
    )


def observed(**overrides):
    """관찰이 완료된 보고값. anyResponseDetected가 null이 아니다."""
    info = report_defaults()
    info["anyResponseDetected"] = True
    info["terminationReason"] = "NORMAL"
    info.update(overrides)
    return info


class PreserveCollectedRiskTest(unittest.TestCase):
    def test_timeout_keeps_confirmed_urgent_condition(self):
        """핵심 회귀: 답을 다 받고 시간만 초과했는데 IMMEDIATE가 사라지면 안 된다."""
        risk = risk_assessment(
            observed(
                urgentConditionReported="YES",
                mobilityStatus="NO",
                reportedResponsiveCount=2,
                terminationReason="TIMEOUT",
            )
        )
        self.assertEqual(risk["riskLevel"], "IMMEDIATE")
        self.assertIn("긴급 상태가 있다고 발화함", risk["riskReasons"])
        self.assertIn("세션 미완료: TIMEOUT", risk["riskReasons"])

    def test_safety_abort_keeps_mobility_verdict(self):
        """가장 위험한 경우 — 위험해서 대피한 상황의 정보가 사라지면 안 된다."""
        risk = risk_assessment(
            observed(
                mobilityStatus="NO",
                urgentConditionReported="UNKNOWN",
                terminationReason="ABORTED_SAFETY",
            )
        )
        self.assertEqual(risk["riskLevel"], "URGENT")
        self.assertIn("세션 미완료: ABORTED_SAFETY", risk["riskReasons"])

    def test_manual_abort_keeps_delayed_verdict(self):
        risk = risk_assessment(
            observed(
                mobilityStatus="YES",
                urgentConditionReported="NO",
                terminationReason="ABORTED_MANUAL",
            )
        )
        self.assertEqual(risk["riskLevel"], "DELAYED")
        self.assertIn("세션 미완료: ABORTED_MANUAL", risk["riskReasons"])

    def test_no_response_stays_immediate_when_incomplete(self):
        risk = risk_assessment(
            observed(anyResponseDetected=False, terminationReason="TIMEOUT")
        )
        self.assertEqual(risk["riskLevel"], "IMMEDIATE")
        self.assertIn(
            "정상 청취 후 음성 응답이 감지되지 않음", risk["riskReasons"]
        )

    def test_reasons_keep_observation_before_termination(self):
        """관찰 근거가 먼저, 절차 정보가 뒤에 온다.

        관제 화면이 첫 근거만 보여줘도 요구조자 상태를 읽을 수 있어야 한다.
        """
        risk = risk_assessment(
            observed(
                urgentConditionReported="YES", terminationReason="TIMEOUT"
            )
        )
        self.assertEqual(risk["riskReasons"][0], "긴급 상태가 있다고 발화함")
        self.assertEqual(risk["riskReasons"][-1], "세션 미완료: TIMEOUT")


class UnobservedSessionStaysUnknownTest(unittest.TestCase):
    """관찰 자체를 못 한 경우는 여전히 UNKNOWN이다. 이 단락은 유지한다."""

    def test_audio_device_error_without_observation(self):
        risk = risk_assessment(
            report_defaults() | {"terminationReason": "AUDIO_DEVICE_ERROR"}
        )
        self.assertEqual(risk["riskLevel"], "UNKNOWN")
        self.assertIn("응답 여부를 관찰하지 못함", risk["riskReasons"])
        self.assertIn("세션 미완료: AUDIO_DEVICE_ERROR", risk["riskReasons"])

    def test_gms_unavailable_without_observation(self):
        risk = risk_assessment(
            report_defaults() | {"terminationReason": "GMS_UNAVAILABLE"}
        )
        self.assertEqual(risk["riskLevel"], "UNKNOWN")

    def test_device_error_after_answers_keeps_verdict(self):
        """장치 오류라도 그 전에 관찰한 값이 있으면 버리지 않는다."""
        risk = risk_assessment(
            observed(
                urgentConditionReported="YES",
                terminationReason="AUDIO_DEVICE_ERROR",
            )
        )
        self.assertEqual(risk["riskLevel"], "IMMEDIATE")


class CompleteTerminationsTest(unittest.TestCase):
    def test_normal_and_unknown_add_no_extra_reason(self):
        for termination in sorted(COMPLETE_TERMINATIONS):
            with self.subTest(termination=termination):
                risk = risk_assessment(
                    observed(
                        urgentConditionReported="YES",
                        terminationReason=termination,
                    )
                )
                self.assertEqual(risk["riskLevel"], "IMMEDIATE")
                self.assertEqual(
                    risk["riskReasons"], ["긴급 상태가 있다고 발화함"]
                )

    def test_rule_version_was_bumped(self):
        """규칙이 바뀌었으므로 버전도 올려야 감사가 가능하다."""
        self.assertEqual(RISK_RULE_VERSION, "voice-risk-v1.1")

    def test_operator_review_is_always_required(self):
        for termination in ("NORMAL", "TIMEOUT", "ABORTED_SAFETY"):
            with self.subTest(termination=termination):
                risk = risk_assessment(
                    observed(terminationReason=termination)
                )
                self.assertTrue(risk["operatorReviewRequired"])


class SessionTimeoutBudgetTest(unittest.TestCase):
    def test_default_budget_has_margin_over_measured_worst_case(self):
        """실측 최대 세션이 111.2초였다. 120초는 마진이 9초뿐이었다."""
        machine = ConversationMachine(
            prompt=lambda question, text: None,
            listen=lambda question, attempt: None,
            interpret=lambda question, text: None,
        )
        self.assertEqual(machine.timeout_seconds, 180)
        self.assertGreater(machine.timeout_seconds, 111.2 * 1.5)

    def test_budget_is_still_injectable(self):
        machine = ConversationMachine(
            prompt=lambda question, text: None,
            listen=lambda question, attempt: None,
            interpret=lambda question, text: None,
            timeout_seconds=5,
        )
        self.assertEqual(machine.timeout_seconds, 5)

    def test_runner_does_not_shadow_the_budget(self):
        """실기 경로가 쓰는 실행기의 예산이 상태머신까지 도달해야 한다.

        179에서 상태머신만 180으로 올렸는데 VoiceSessionRunner가 자기 기본값
        120을 그대로 넘겨, pipeline·ros_node 두 경로 모두 반영되지 않았다.
        pipeline·ros_node는 timeout_seconds를 넘기지 않으므로 기본값이 그대로 쓰인다.
        """
        budgets = []

        class Spy(ConversationMachine):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                budgets.append(self.timeout_seconds)

            def run(self):
                return SessionResult()

        runner = VoiceSessionRunner(_silent_dependencies(), listen_delay=0)
        with patch("sentinel_voice.session_runner.ConversationMachine", Spy):
            runner.run()

        self.assertEqual(budgets, [SESSION_TIMEOUT_SECONDS])


if __name__ == "__main__":
    unittest.main()

import unittest

from sentinel_voice.conversation import (
    ASKED_QUESTIONS,
    AudioObservation,
    ConversationMachine,
    QuestionCode,
    ResponseClass,
    SessionState,
    classify_response,
)
from sentinel_voice.guide_audio import GUIDE_ASSETS, GuideCode


class ConversationMachineTest(unittest.TestCase):
    def machine(self, observations, abort=lambda: None, unrecognized=()):
        prompts = []

        def listen(question, attempt):
            return observations.get(
                (question, attempt),
                observations.get(question, AudioObservation(False)),
            )

        def interpret(question, text):
            if question in unrecognized:
                return None
            values = {
                QuestionCode.INTRO: True,
                QuestionCode.COUNT: 2,
                QuestionCode.MOBILITY: "NO",
                QuestionCode.URGENT: "YES",
            }
            return values.get(question)

        return (
            ConversationMachine(
                prompt=lambda code, text: prompts.append((code, text)),
                listen=listen,
                interpret=interpret,
                abort_requested=abort,
            ),
            prompts,
        )

    def test_normal_completion(self):
        observations = {
            question: AudioObservation(True, "정상 응답")
            for question in QuestionCode
            if question != QuestionCode.CLOSING
        }
        machine, prompts = self.machine(observations)
        result = machine.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual([code for code, _ in prompts], list(ASKED_QUESTIONS))
        # 종료 안내는 발신 상태를 아는 전송 단계가 한다. 상태머신은 하지 않는다.
        self.assertNotEqual(
            prompts[-1][1],
            GUIDE_ASSETS[GuideCode.REPORT_SUCCEEDED_DEPARTURE].text,
        )
        self.assertEqual(result.fields["reportedResponsiveCount"], 2)
        self.assertFalse(result.operator_review_required)

    def test_partial_no_response_stores_unknown_and_continues(self):
        observations = {
            QuestionCode.INTRO: AudioObservation(True, "네"),
            QuestionCode.COUNT: AudioObservation(False),
            QuestionCode.MOBILITY: AudioObservation(True, "아니오"),
            QuestionCode.URGENT: AudioObservation(True, "없어요"),
        }
        machine, _ = self.machine(observations)
        result = machine.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.fields["reportedResponsiveCount"], "UNKNOWN")
        self.assertEqual(result.fields["mobilityStatus"], "NO")

    def test_intro_retries_once_for_total_no_response(self):
        machine, prompts = self.machine({})
        result = machine.run()

        intro_turns = [
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        ]
        self.assertEqual(len(intro_turns), 2)
        self.assertFalse(result.fields["anyResponseDetected"])
        self.assertIn(SessionState.RETRYING, result.state_log)
        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(
            prompts[1],
            (
                QuestionCode.INTRO,
                GUIDE_ASSETS[GuideCode.RETRY_NO_RESPONSE].text,
            ),
        )

    def test_question_order_asks_urgent_first(self):
        # 조기 종료 시 가장 중요한 부상 정보부터 확보한다(S15P11A301-146 v2).
        self.assertEqual(
            ASKED_QUESTIONS,
            (
                QuestionCode.INTRO,
                QuestionCode.URGENT,
                QuestionCode.MOBILITY,
                QuestionCode.COUNT,
            ),
        )

    def test_unrecognized_answer_moves_on_without_retry(self):
        # 값 미확정 답변은 되묻지 않는다(S15P11A301-201). UNKNOWN으로 두고
        # 다음 질문으로 진행하며, 원문은 세션 기록이 보존한다.
        observations = {
            question: AudioObservation(True, "정상 응답")
            for question in ASKED_QUESTIONS
        }
        machine, _ = self.machine(
            observations, unrecognized={QuestionCode.MOBILITY}
        )
        result = machine.run()

        mobility_turns = [
            turn
            for turn in result.turns
            if turn.question == QuestionCode.MOBILITY
        ]
        self.assertEqual(len(mobility_turns), 1)
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertNotIn(SessionState.RETRYING, result.state_log)
        self.assertEqual(result.fields["reportedResponsiveCount"], 2)
        self.assertTrue(result.operator_review_required)
        self.assertEqual(result.state, SessionState.COMPLETED)

    def test_stt_failure_moves_on_without_retry(self):
        # STT 실패도 되묻지 않는다. RETRY_UNCLEAR 문구 삭제(146 v2)에 따라
        # 재질문 경로는 INTRO 무응답 하나만 남는다.
        observations = {
            question: AudioObservation(True, "정상 응답")
            for question in ASKED_QUESTIONS
        }
        observations[QuestionCode.URGENT] = AudioObservation(True, "")
        machine, _ = self.machine(observations)
        result = machine.run()

        urgent_turns = [
            turn
            for turn in result.turns
            if turn.question == QuestionCode.URGENT
        ]
        self.assertEqual(len(urgent_turns), 1)
        self.assertEqual(result.fields["urgentConditionReported"], "UNKNOWN")
        self.assertNotIn(SessionState.RETRYING, result.state_log)
        self.assertTrue(result.operator_review_required)

    def test_manual_abort(self):
        calls = iter([None, SessionState.ABORTED_MANUAL])
        machine, _ = self.machine({}, abort=lambda: next(calls))
        result = machine.run()

        self.assertEqual(result.state, SessionState.ABORTED_MANUAL)
        self.assertEqual(result.termination_reason, "ABORTED_MANUAL")

    def test_safety_abort(self):
        calls = iter([None, SessionState.ABORTED_SAFETY])
        machine, _ = self.machine({}, abort=lambda: next(calls))
        result = machine.run()

        self.assertEqual(result.state, SessionState.ABORTED_SAFETY)
        self.assertEqual(result.termination_reason, "ABORTED_SAFETY")

    def test_audio_error_is_not_recorded_as_no_response(self):
        observations = {
            QuestionCode.INTRO: AudioObservation(
                voice_detected=False, audio_error=True
            )
        }
        machine, _ = self.machine(observations)
        result = machine.run()

        self.assertEqual(result.state, SessionState.FAILED_AUDIO)
        self.assertEqual(result.termination_reason, "AUDIO_DEVICE_ERROR")
        self.assertEqual(result.turns, [])
        self.assertIsNone(result.fields["anyResponseDetected"])
        self.assertEqual(
            result.fields["terminationReason"], "AUDIO_DEVICE_ERROR"
        )

    def test_timeout_records_reason(self):
        machine, _ = self.machine({})
        # 예산을 하드코딩하지 않는다. S15P11A301-179에서 120초를 180초로 올렸을 때
        # 상수를 박아 둔 이 테스트가 조용히 통과하지 않고 깨졌다.
        ticks = iter([0.0, 0.0, machine.timeout_seconds + 1])
        machine.clock = lambda: next(ticks)
        result = machine.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.termination_reason, "TIMEOUT")

    def test_four_response_classes_and_stt_failure_review(self):
        cases = [
            (AudioObservation(False), None, ResponseClass.NO_VOICE_DETECTED),
            (
                AudioObservation(True, ""),
                None,
                ResponseClass.VOICE_DETECTED_STT_FAILED,
            ),
            (
                AudioObservation(True, "모호한 답"),
                None,
                ResponseClass.RESPONSE_UNRECOGNIZED,
            ),
            (
                AudioObservation(True, "두 명"),
                2,
                ResponseClass.ANSWER_STRUCTURED,
            ),
        ]
        for observation, value, expected in cases:
            with self.subTest(expected=expected):
                actual, review = classify_response(observation, value)
                self.assertEqual(actual, expected)
                if expected == ResponseClass.VOICE_DETECTED_STT_FAILED:
                    self.assertTrue(review)


if __name__ == "__main__":
    unittest.main()

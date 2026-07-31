"""알아듣지 못한 응답은 한 번 더 묻는다 (S15P11A301-165).

2026-07-31 로컬 시험에서 요구조자가 작은 소리로 "움직일 수 있어요"라고 답했는데
STT가 "이럴 수 있어요?"로 받았다. GMS는 값을 확정하지 못했고, 시스템은 **다시 묻지
않고 다음 질문으로 넘어가** `mobilityStatus=UNKNOWN`으로 보고했다.

이 상황을 위한 승인 문구가 이미 있었다.

    RETRY_UNCLEAR = "목소리가 잘 들리지 않았습니다. 천천히 다시 말씀해 주세요."

그런데 코드가 한 번도 쓰지 않았다. 자산은 있고 배선이 없었다. 배선만 했다.

중상자일수록 크게 말할 수 없으므로(§11-5) 약한 발화에서 값을 놓치는 경로는 현장
조건 그 자체다.
"""

import unittest

from sentinel_voice.conversation import (
    RETRY_POLICY,
    UNCLEAR_RESPONSES,
    AudioObservation,
    ConversationMachine,
    QuestionCode,
    ResponseClass,
)
from sentinel_voice.guide_audio import GUIDE_ASSETS, GuideCode


ANSWERS = {
    QuestionCode.INTRO: True,
    QuestionCode.COUNT: 2,
    QuestionCode.MOBILITY: "YES",
    QuestionCode.URGENT: "NO",
}


def build(observations):
    """관찰값을 (질문, 시도)별로 주입한다. 해석은 항상 성공한다."""
    played = []

    def listen(question, attempt):
        return observations.get(
            (question, attempt),
            observations.get(question, AudioObservation(True, "답")),
        )

    def interpret(question, text):
        return ANSWERS.get(question)

    return (
        ConversationMachine(
            prompt=lambda code, text: played.append(text),
            listen=listen,
            interpret=interpret,
        ),
        played,
    )


UNCLEAR_TEXT = GUIDE_ASSETS[GuideCode.RETRY_UNCLEAR].text
NO_RESPONSE_TEXT = GUIDE_ASSETS[GuideCode.RETRY_NO_RESPONSE].text


class AssetIsActuallyWiredTest(unittest.TestCase):
    """자산이 다시 놀지 않도록 배선 자체를 고정한다."""

    def test_retry_unclear_is_used_by_the_policy(self):
        guides = {guide for _, guide in RETRY_POLICY.values()}
        self.assertIn(GuideCode.RETRY_UNCLEAR, guides)
        self.assertIn(GuideCode.RETRY_NO_RESPONSE, guides)

    def test_every_asked_question_has_a_policy(self):
        for question in QuestionCode:
            if question == QuestionCode.CLOSING:
                continue
            with self.subTest(question=question.value):
                self.assertIn(question, RETRY_POLICY)

    def test_unclear_set_covers_both_understanding_failures(self):
        self.assertEqual(
            UNCLEAR_RESPONSES,
            {
                ResponseClass.VOICE_DETECTED_STT_FAILED,
                ResponseClass.RESPONSE_UNRECOGNIZED,
            },
        )


class RetryOnUnrecognizedTest(unittest.TestCase):
    def test_second_attempt_recovers_the_value(self):
        """실제로 겪은 경로. 1차에서 못 알아듣고 2차에서 확정한다."""
        calls = {"n": 0}

        def interpret(question, text):
            if question != QuestionCode.MOBILITY:
                return {
                    QuestionCode.INTRO: True,
                    QuestionCode.COUNT: 2,
                    QuestionCode.URGENT: "NO",
                }[question]
            calls["n"] += 1
            return None if calls["n"] == 1 else "YES"

        played = []
        conversation = ConversationMachine(
            prompt=lambda code, text: played.append(text),
            listen=lambda question, attempt: AudioObservation(True, "답"),
            interpret=interpret,
        )
        result = conversation.run()

        self.assertIn(UNCLEAR_TEXT, played)
        self.assertEqual(result.fields["mobilityStatus"], "YES")

    def test_stt_failure_also_retries(self):
        conversation, played = build(
            {(QuestionCode.MOBILITY, 1): AudioObservation(True, "")}
        )
        result = conversation.run()

        self.assertIn(UNCLEAR_TEXT, played)
        self.assertEqual(result.fields["mobilityStatus"], "YES")

    def test_retry_happens_once_per_question(self):
        """두 번째도 실패하면 UNKNOWN으로 두고 넘어간다. 무한히 묻지 않는다."""
        conversation, played = build(
            {QuestionCode.MOBILITY: AudioObservation(True, "")}
        )
        result = conversation.run()

        self.assertEqual(played.count(UNCLEAR_TEXT), 1)
        mobility = [
            turn
            for turn in result.turns
            if turn.question == QuestionCode.MOBILITY
        ]
        self.assertEqual([turn.attempt for turn in mobility], [1, 2])
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(result.operator_review_required)

    def test_structured_answer_is_not_re_asked(self):
        conversation, played = build({})
        conversation.run()
        self.assertNotIn(UNCLEAR_TEXT, played)

    def test_silence_on_a_field_question_does_not_use_unclear_wording(self):
        """무음에는 "목소리가 잘 들리지 않았습니다"가 맞지 않는다.

        들리지 않은 것이 아니라 아무 소리도 없었다. 그 경로는 재질문하지 않는다.
        """
        conversation, played = build(
            {QuestionCode.MOBILITY: AudioObservation(False)}
        )
        result = conversation.run()

        self.assertNotIn(UNCLEAR_TEXT, played)
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")

    def test_intro_still_uses_the_no_response_wording(self):
        conversation, played = build({QuestionCode.INTRO: AudioObservation(False)})
        conversation.run()
        self.assertIn(NO_RESPONSE_TEXT, played)
        self.assertNotIn(UNCLEAR_TEXT, played)

    def test_intro_does_not_retry_on_unclear(self):
        """INTRO는 발화 존재가 답이다. 이해 실패도 응답으로 집계된다."""
        conversation, played = build(
            {(QuestionCode.INTRO, 1): AudioObservation(True, "")}
        )
        result = conversation.run()

        self.assertNotIn(UNCLEAR_TEXT, played)
        self.assertIs(result.fields["anyResponseDetected"], True)


class RetryBudgetTest(unittest.TestCase):
    def test_worst_case_listen_count_is_bounded(self):
        """세 질문이 모두 재질문해도 청취 횟수가 예산을 넘지 않아야 한다."""
        listens = []

        conversation = ConversationMachine(
            prompt=lambda code, text: None,
            listen=lambda question, attempt: listens.append((question, attempt))
            or AudioObservation(True, ""),
            interpret=lambda question, text: None,
        )
        conversation.run()

        # INTRO는 이해 실패로 응답 판정되어 재질문하지 않는다.
        # COUNT·MOBILITY·URGENT가 각각 2회 = 7회.
        self.assertEqual(len(listens), 7)
        self.assertLessEqual(len(listens), 8, "청취 횟수가 예산 산정을 넘었다")


if __name__ == "__main__":
    unittest.main()

"""재질문은 INTRO 무응답 1회뿐이다 (S15P11A301-201).

이 파일의 전신(test_unclear_retry.py)은 반대 방향의 명세였다. 2026-07-31 로컬
시험에서 약한 발화("움직일 수 있어요" → STT "이럴 수 있어요?")가 값 확정에
실패해 조용히 UNKNOWN이 됐고, S15P11A301-165가 RETRY_UNCLEAR 재질문을 배선했다.

하루 뒤 이 배선을 걷어냈다. 컨설팅 지적 — 한시가 급한 상황에 이상하게 말했다고
다시 말해 달라는 요구가 이질적이다. 값 미확정(STT 실패·해석 실패)은 되묻지 않고
UNKNOWN으로 진행하며, 원문 전사·녹음이 세션 기록에 남아 관제가 직접 판단한다
(S15P11A301-202 블랙박스가 보상 통제).

트레이드오프를 숨기지 않는다: 약한 발화의 값은 이제 기계 보고에서 UNKNOWN으로
남는다. 무음 재질문(INTRO)은 유지한다 — "안 들리는 경우"에 되묻는 것은 사람
사이에서도 자연스럽다.
"""

import unittest

from sentinel_voice.conversation import (
    ASKED_QUESTIONS,
    PROMPTS,
    RETRY_POLICY,
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

NO_RESPONSE_TEXT = GUIDE_ASSETS[GuideCode.RETRY_NO_RESPONSE].text

# 상태머신이 재생해도 되는 문구의 전부. 질문 4개와 무응답 재질문뿐이다.
# RETRY_UNCLEAR 등 다른 문구가 여기 끼어들면 정책 위반이다.
ALLOWED_TEXTS = set(PROMPTS.values()) | {NO_RESPONSE_TEXT}


def build(observations, interpret=None):
    """관찰값을 (질문, 시도)별로 주입한다. 해석은 기본적으로 항상 성공한다."""
    played = []

    def listen(question, attempt):
        return observations.get(
            (question, attempt),
            observations.get(question, AudioObservation(True, "답")),
        )

    def default_interpret(question, text):
        return ANSWERS.get(question)

    return (
        ConversationMachine(
            prompt=lambda code, text: played.append(text),
            listen=listen,
            interpret=interpret or default_interpret,
        ),
        played,
    )


class RetryPolicyShapeTest(unittest.TestCase):
    """정책 자체를 고정한다. 재질문 경로는 INTRO 무응답 하나다."""

    def test_policy_covers_only_intro(self):
        self.assertEqual(set(RETRY_POLICY), {QuestionCode.INTRO})

    def test_intro_retry_targets_no_voice_with_no_response_wording(self):
        retry_classes, retry_guide = RETRY_POLICY[QuestionCode.INTRO]
        self.assertEqual(
            retry_classes, {ResponseClass.NO_VOICE_DETECTED}
        )
        self.assertEqual(retry_guide, GuideCode.RETRY_NO_RESPONSE)


class NoRetryOnUnclearTest(unittest.TestCase):
    def test_unrecognized_answer_moves_on_with_unknown(self):
        """165가 배선했던 경로의 반전. 해석 실패는 한 번만 듣고 진행한다."""
        calls = {"n": 0}

        def interpret(question, text):
            if question != QuestionCode.MOBILITY:
                return ANSWERS.get(question)
            calls["n"] += 1
            return None

        conversation, played = build({}, interpret=interpret)
        result = conversation.run()

        self.assertEqual(calls["n"], 1)
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(set(played) <= ALLOWED_TEXTS)
        self.assertTrue(result.operator_review_required)

    def test_stt_failure_moves_on_with_unknown(self):
        conversation, played = build(
            {QuestionCode.MOBILITY: AudioObservation(True, "")}
        )
        result = conversation.run()

        mobility = [
            turn
            for turn in result.turns
            if turn.question == QuestionCode.MOBILITY
        ]
        self.assertEqual([turn.attempt for turn in mobility], [1])
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(set(played) <= ALLOWED_TEXTS)

    def test_silence_on_a_field_question_does_not_retry(self):
        """필드 질문 무음도 재질문하지 않는다. 원래부터 그랬다."""
        conversation, played = build(
            {QuestionCode.MOBILITY: AudioObservation(False)}
        )
        result = conversation.run()

        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(set(played) <= ALLOWED_TEXTS)

    def test_intro_still_retries_no_response(self):
        conversation, played = build(
            {QuestionCode.INTRO: AudioObservation(False)}
        )
        conversation.run()
        self.assertIn(NO_RESPONSE_TEXT, played)

    def test_intro_counts_unclear_as_response(self):
        """INTRO는 발화 존재가 답이다. 이해 실패도 응답으로 집계된다."""
        conversation, played = build(
            {(QuestionCode.INTRO, 1): AudioObservation(True, "")}
        )
        result = conversation.run()

        self.assertNotIn(NO_RESPONSE_TEXT, played)
        self.assertIs(result.fields["anyResponseDetected"], True)


class RetryBudgetTest(unittest.TestCase):
    def test_worst_case_listen_count_is_question_count(self):
        """전 질문 이해 실패여도 청취는 질문당 1회다. 세션 최악 시간의 근거."""
        listens = []

        conversation = ConversationMachine(
            prompt=lambda code, text: None,
            listen=lambda question, attempt: listens.append((question, attempt))
            or AudioObservation(True, ""),
            interpret=lambda question, text: None,
        )
        conversation.run()

        self.assertEqual(len(listens), len(ASKED_QUESTIONS))

    def test_worst_case_with_total_silence_is_two_listens(self):
        """완전 무응답이면 INTRO 2회 청취 후 남은 질문을 버린다(§11-4)."""
        listens = []

        conversation = ConversationMachine(
            prompt=lambda code, text: None,
            listen=lambda question, attempt: listens.append((question, attempt))
            or AudioObservation(False),
            interpret=lambda question, text: None,
        )
        conversation.run()

        self.assertEqual(
            [question for question, _ in listens],
            [QuestionCode.INTRO, QuestionCode.INTRO],
        )


if __name__ == "__main__":
    unittest.main()

"""질문 맥락 전달과 STT 프라이밍 제거 (S15P11A301-251).

두 가지 변경을 고정한다.

  ① LLM에 로봇이 물은 문구를 넘긴다 — 뭉개진 받아쓰기를 그 질문에 대한 답으로
     되돌릴 근거가 된다. 도메인 발화 16개 × 4조건에서 슬롯 정확도 45~47% → 52~55%
     (3회 반복, 망친 것 0건).
  ② 운영 프라이밍을 제거한다 — 슬롯 이득 0, 목적 미달성, 무발화에서 허위
     긴급 보고 15.8%.

근거: docs/measurements/STT-오류율-실측.md §3, Jira S15P11A301-251.
"""

import unittest
from unittest.mock import patch

from sentinel_voice import config
from sentinel_voice.conversation import PROMPTS, QuestionCode
from sentinel_voice.llm import extract_with_status, llm_extract
from sentinel_voice.safety import is_valid_stt


def _fake_response(payload: str):
    message = type("Message", (), {"content": payload})()
    choice = type("Choice", (), {"message": message})()
    return type("Response", (), {"choices": [choice]})()


VALID_JSON = (
    '{"reportedResponsiveCount":null,'
    '"mobilityStatus":"NO",'
    '"urgentConditionReported":"YES"}'
)


class QuestionContextTest(unittest.TestCase):
    """LLM이 무엇을 물었는지 알아야 뭉개진 글자를 되돌릴 수 있다."""

    @patch("sentinel_voice.llm._gms")
    def test_question_is_sent_to_the_model(self, gms):
        gms.return_value.chat.completions.create.return_value = _fake_response(VALID_JSON)
        llm_extract("비가 계속 나요", "다친 곳이 있으십니까?")
        sent = gms.return_value.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ]
        self.assertIn("다친 곳이 있으십니까?", sent)
        self.assertIn("비가 계속 나요", sent)

    @patch("sentinel_voice.llm._gms")
    def test_placeholder_is_replaced_even_without_question(self, gms):
        """질문을 넘기지 않아도 자리표시자가 그대로 남지 않아야 한다."""
        gms.return_value.chat.completions.create.return_value = _fake_response(VALID_JSON)
        llm_extract("다리를 다쳤어요")
        sent = gms.return_value.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ]
        self.assertNotIn("{question_text}", sent)
        self.assertNotIn("{input_text}", sent)

    @patch("sentinel_voice.llm.llm_extract", return_value={"mobilityStatus": "NO"})
    def test_question_passes_through_extract_with_status(self, llm):
        extract_with_status("일로 나려니까 너무 아파요", "움직일 수 있습니까?")
        self.assertEqual(llm.call_args.args[1], "움직일 수 있습니까?")


class PromptContractTest(unittest.TestCase):
    """되돌리기를 허용하되 넘지 않는 선이 프롬프트에 남아 있어야 한다."""

    def setUp(self):
        self.prompt = config.PROMPT_PATH.read_text(encoding="utf-8")

    def test_question_placeholder_exists(self):
        self.assertIn("{question_text}", self.prompt)
        self.assertIn("{input_text}", self.prompt)

    def test_negation_must_not_be_flipped(self):
        """가장 중요한 선이다. "없습니다"가 "있습니다"로 읽히면 정반대 보고가 나간다."""
        self.assertIn("부정을 뒤집지 않는다", self.prompt)

    def test_whole_different_utterance_must_not_be_recovered(self):
        """한두 음절이 어긋난 경우만 되돌린다. 통째로 다른 말은 손대지 않는다."""
        self.assertIn("통째로 다른 말이면 되돌리지 않는다", self.prompt)


class SttPrimingRemovedTest(unittest.TestCase):
    def test_is_valid_stt_tolerates_no_prompt(self):
        """프라이밍이 없어도 판정이 깨지지 않아야 한다."""
        valid, reason = is_valid_stt("다리를 다쳤어요", 0.1, None)
        self.assertTrue(valid, reason)

    def test_genuine_urgent_speech_is_no_longer_rejected(self):
        """프라이밍이 있을 때 이 발화는 「프롬프트 복사」로 거부됐다.

        요구조자가 실제로 프롬프트와 같은 낱말들을 말하면 적중 3개로 정상 발화가
        버려졌다. 프라이밍을 없앤 지금은 통과해야 한다.
        """
        valid, reason = is_valid_stt(
            "살려주세요 도와주세요 다쳤어요", 0.2, None
        )
        self.assertTrue(valid, reason)

    def test_other_guards_still_work(self):
        """프라이밍 제거가 나머지 환각 가드를 무력화하지 않아야 한다."""
        self.assertFalse(is_valid_stt("", 0.1, None)[0])
        self.assertFalse(is_valid_stt("네", 0.9, None)[0])
        self.assertFalse(
            is_valid_stt("가스 가스 가스 가스 가스", 0.1, None)[0]
        )


class RunnerWiringTest(unittest.TestCase):
    def test_every_listening_question_has_asked_wording(self):
        """추출에 넘길 문구가 모든 질문에 있어야 한다. 없으면 되돌리기 근거가 빈다."""
        for question in (
            QuestionCode.INTRO,
            QuestionCode.COUNT,
            QuestionCode.MOBILITY,
            QuestionCode.URGENT,
        ):
            with self.subTest(question=question):
                self.assertTrue(PROMPTS.get(question))


if __name__ == "__main__":
    unittest.main()

"""무응답이면 즉시 종료하고, 종료 안내는 한 곳에서만 한다 (S15P11A301-165).

2026-07-31 로컬 시험에서 두 가지가 드러났다.

  1. 시나리오 C에서 재질문까지 반응이 없었는데도 COUNT·MOBILITY·URGENT를 끝까지
     재생했다. 반응 없는 요구조자 앞에서 약 50초를 더 썼다(E2E 69.9초).
  2. `"구조 요청을 관제에 전달하고 있습니다"`가 두 번 나왔다.

두 번째의 원인은 **안내하는 주체가 둘이었던 것**이다. 상태머신이 CLOSING 단계에서
진행형 문구를 재생했는데, 그 시점에는 아직 발신하지 않아 발신 상태를 알 수 없었다.
그래서 문구를 추측해 박아 두었고, 발신 후 실제 상태로 한 번 더 안내하면서 두 문구가
같아지는 경로에서 중복이 났다.

**상태머신에서 추측을 없앴다.** 종료 안내는 발신 상태를 아는 전송 단계가 한 번만 한다.
"""

import unittest

from sentinel_voice.conversation import (
    ASKED_QUESTIONS,
    PROMPTS,
    AudioObservation,
    ConversationMachine,
    QuestionCode,
    ResponseClass,
    SessionState,
)
from sentinel_voice.guide_audio import GUIDE_ASSETS, GuideCode
from sentinel_voice.safety import risk_assessment


def machine(observations, *, values=None):
    """관찰값을 주입한 상태머신과 재생 기록을 돌려준다."""
    played = []

    def listen(question, attempt):
        return observations.get(
            (question, attempt),
            observations.get(question, AudioObservation(False)),
        )

    def interpret(question, text):
        table = values or {
            QuestionCode.INTRO: True,
            QuestionCode.COUNT: 2,
            QuestionCode.MOBILITY: "YES",
            QuestionCode.URGENT: "NO",
        }
        return table.get(question)

    return (
        ConversationMachine(
            prompt=lambda code, text: played.append(code),
            listen=listen,
            interpret=interpret,
        ),
        played,
    )


class NoResponseEndsSessionTest(unittest.TestCase):
    def test_total_silence_skips_remaining_questions(self):
        conversation, played = machine({})
        conversation.run()

        # INTRO 두 번(최초 + 재질문)에서 끝난다.
        self.assertEqual(played, [QuestionCode.INTRO, QuestionCode.INTRO])
        self.assertNotIn(QuestionCode.COUNT, played)
        self.assertNotIn(QuestionCode.MOBILITY, played)
        self.assertNotIn(QuestionCode.URGENT, played)

    def test_report_is_unchanged_by_early_exit(self):
        """조기 종료가 보고 내용을 바꾸면 안 된다. 무응답 판정과 등급은 그대로다."""
        conversation, _ = machine({})
        result = conversation.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.termination_reason, "NORMAL")
        self.assertIs(result.fields["anyResponseDetected"], False)
        self.assertEqual(result.fields["reportedResponsiveCount"], None)
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertEqual(result.fields["urgentConditionReported"], "UNKNOWN")
        self.assertEqual(risk_assessment(result.fields)["riskLevel"], "IMMEDIATE")

    def test_only_intro_turns_are_recorded(self):
        conversation, _ = machine({})
        result = conversation.run()
        self.assertEqual(
            [(turn.question, turn.attempt) for turn in result.turns],
            [(QuestionCode.INTRO, 1), (QuestionCode.INTRO, 2)],
        )

    def test_stt_failure_does_not_end_the_session(self):
        """사람을 들었으나 전사에 실패한 경우는 무응답이 아니다(명세 33-3).

        여기서 조기 종료하면 STT 실패를 무응답과 같이 취급하는 것이 된다.
        """
        conversation, played = machine(
            {QuestionCode.INTRO: AudioObservation(True, "")}
        )
        result = conversation.run()

        self.assertEqual(played, list(ASKED_QUESTIONS))
        self.assertIs(result.fields["anyResponseDetected"], True)
        self.assertEqual(
            result.turns[0].response_class,
            ResponseClass.VOICE_DETECTED_STT_FAILED,
        )

    def test_late_response_on_retry_continues_the_session(self):
        """1차 무응답, 재질문에서 응답하면 남은 질문을 계속한다."""
        conversation, played = machine(
            {
                (QuestionCode.INTRO, 1): AudioObservation(False),
                (QuestionCode.INTRO, 2): AudioObservation(True, "네"),
                QuestionCode.COUNT: AudioObservation(True, "두 명"),
                QuestionCode.MOBILITY: AudioObservation(True, "네"),
                QuestionCode.URGENT: AudioObservation(True, "없어요"),
            }
        )
        result = conversation.run()

        self.assertIn(QuestionCode.URGENT, played)
        self.assertIs(result.fields["anyResponseDetected"], True)
        self.assertEqual(result.fields["reportedResponsiveCount"], 2)

    def test_normal_session_still_asks_everything(self):
        """회귀 — 응답이 있으면 5단계가 그대로 돈다."""
        conversation, played = machine(
            {
                question: AudioObservation(True, "답")
                for question in QuestionCode
                if question != QuestionCode.CLOSING
            }
        )
        result = conversation.run()

        self.assertEqual(played, list(ASKED_QUESTIONS))
        self.assertEqual(result.fields["mobilityStatus"], "YES")


class SingleClosingAnnouncerTest(unittest.TestCase):
    """종료 안내는 발신 상태를 아는 한 곳만 한다."""

    def test_state_machine_does_not_announce_the_report_status(self):
        """상태머신은 발신 상태를 알 수 없으므로 추측하지 않는다.

        이전에는 `PROMPTS[CLOSING]`에 진행형 문구를 박아 두었다. 그것이 중복의
        원인이었다. 이 단정이 깨지면 중복이 되살아난다.
        """
        self.assertNotIn(QuestionCode.CLOSING, PROMPTS)
        self.assertNotIn(QuestionCode.CLOSING, ASKED_QUESTIONS)

    def test_no_guide_text_is_reachable_from_the_state_machine_twice(self):
        """상태머신이 재생하는 문구에 발신 상태 안내가 섞여 있지 않다."""
        delivery_guides = {
            GUIDE_ASSETS[code].text
            for code in (
                GuideCode.REPORT_PENDING,
                GuideCode.REPORT_SUCCEEDED,
                GuideCode.REPORT_SUCCEEDED_DEPARTURE,
                GuideCode.NETWORK_WAIT,
            )
        }
        self.assertEqual(set(PROMPTS.values()) & delivery_guides, set())

    def test_pipeline_announces_exactly_once(self):
        from sentinel_voice import pipeline

        spoken = []
        original = pipeline.speak
        pipeline.speak = lambda text, **kwargs: spoken.append(text)
        try:
            pipeline.queue_and_announce({})
        finally:
            pipeline.speak = original
        self.assertEqual(len(spoken), 1)

    def test_victim_still_hears_a_closing_announcement_when_silent(self):
        """조기 종료해도 아무 말 없이 떠나지 않는다.

        상태머신은 안내하지 않지만, 그 뒤 전송 단계가 반드시 한 번 안내한다.
        """
        from sentinel_voice import pipeline

        conversation, played = machine({})
        result = conversation.run()
        self.assertNotIn(QuestionCode.CLOSING, played)

        spoken = []
        original = pipeline.speak
        pipeline.speak = lambda text, **kwargs: spoken.append(text)
        try:
            pipeline.queue_and_announce(dict(result.fields))
        finally:
            pipeline.speak = original
        self.assertEqual(len(spoken), 1)


if __name__ == "__main__":
    unittest.main()

"""로봇 안내 음성이 요구조자 응답으로 오인되는 경로 차단 (S15P11A301-165).

2026-07-30 젯슨 육성 테스트에서 시나리오 C(완전 무응답)만 3차까지 재현에 실패했다.
INTRO 재질문 청취가 `NOVOICE`가 아니라 `STTFAIL`로 분류됐다. 코드에는 세 구멍이 있다.

  1. AEC가 없어 스피커 출력이 마이크로 유입된다.
  2. 재생 종료 판정(`sd.wait()`)이 실제 가청 종료보다 이르다. A2DP 싱크 버퍼가
     100~250ms 더 있어, 반환 후에도 안내 꼬리가 재생되는 동안 녹음이 시작된다.
  3. `is_valid_stt`의 프롬프트 복사 가드가 안내 문구와는 대조하지 않는다.

**가장 중요한 검증은 등급까지 이어지는지다.** 에코를 `VOICE_DETECTED_STT_FAILED`로
두면 `anyResponseDetected`가 여전히 true가 되어, 의식 없는 요구조자를 "응답 있음"으로
보고한다. 그래서 에코는 `NO_VOICE_DETECTED`로 분류해야 한다.
"""

import unittest

import numpy as np

from sentinel_voice import config
from sentinel_voice.conversation import QuestionCode, ResponseClass, SessionState
from sentinel_voice.guide_audio import (
    GUIDE_ASSETS,
    GUIDE_BY_TEXT,
    GuideCode,
    PlaybackResult,
    PlaybackStatus,
)
from sentinel_voice.safety import guide_echo_match, risk_assessment
from sentinel_voice.session_runner import SessionDependencies, VoiceSessionRunner


class StubPlayer:
    def play_text(self, text, **kwargs):
        return PlaybackResult(GUIDE_BY_TEXT.get(text), PlaybackStatus.PLAYED, "")


def speech(level=0.2, seconds=1.0):
    return np.full(int(config.FS * seconds), level, dtype=np.float32)


class StubExtraction:
    def __init__(self, extraction, source="GMS"):
        self.extraction = extraction
        self.source = source


def build_runner(*, text, sleeps=None, listen_delay=None):
    """모든 청취에서 같은 텍스트가 들리는 세션. 에코 상황을 재현한다."""
    deps = SessionDependencies(
        record=lambda seconds: speech(),
        has_speech=lambda wav: True,
        transcribe=lambda wav: (text, 0.1),
        extract=lambda value: StubExtraction(
            {
                "reportedResponsiveCount": 2,
                "mobilityStatus": "NO",
                "urgentConditionReported": "YES",
            }
        ),
        player=StubPlayer(),
    )
    return VoiceSessionRunner(
        deps,
        sleep=(sleeps.append if sleeps is not None else lambda seconds: None),
        listen_delay=listen_delay,
    )


class GuideEchoMatchTest(unittest.TestCase):
    """완료 기준: 승인 안내 문구 10개 전부를 에코로 판정해야 한다."""

    def test_every_approved_guide_text_is_detected(self):
        self.assertEqual(len(GUIDE_ASSETS), len(GuideCode))
        for code, asset in GUIDE_ASSETS.items():
            with self.subTest(code=code.value):
                is_echo, matched = guide_echo_match(asset.text, GUIDE_BY_TEXT)
                self.assertTrue(is_echo, f"{code.value} 문구가 통과됐다")
                self.assertEqual(matched, asset.text)

    def test_partial_tail_is_detected(self):
        """에코는 온전한 문장이 아니라 꼬리 조각으로 들어온다."""
        fragments = [
            "말이 들리면 대답해",
            "지금 스스로 움직일 수 있나요",
            "잠시만 기다려 주세요",
            "연결되는 대로 구조 요청을 전달하겠습니다",
        ]
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                is_echo, _ = guide_echo_match(fragment, GUIDE_BY_TEXT)
                self.assertTrue(is_echo, f"조각 {fragment!r}이 통과됐다")

    def test_spacing_differences_do_not_matter(self):
        """STT 띄어쓰기는 원문과 다르다."""
        is_echo, _ = guide_echo_match(
            "지금스스로 움직일수있나요", GUIDE_BY_TEXT
        )
        self.assertTrue(is_echo)

    def test_real_victim_answers_survive(self):
        """가장 위험한 오작동 — 실제 응답을 에코로 삼키면 안 된다."""
        answers = [
            "네",
            "네 들려요",
            "여기 두 명이요",
            "두 명이고 움직일 수 없어요",
            "다리를 다쳐서 못 움직여요",
            "숨쉬기 어려워요",
            "피가 많이 나요",
            "저 말고 대답할 수 있는 사람은 없어요",
            "살려주세요",
        ]
        for answer in answers:
            with self.subTest(answer=answer):
                is_echo, matched = guide_echo_match(answer, GUIDE_BY_TEXT)
                self.assertFalse(is_echo, f"{answer!r}이 에코로 판정됐다 → {matched!r}")

    def test_answer_that_reuses_the_question_words_survives(self):
        """가장 좁은 케이스. ASK_MOBILITY가 "지금 스스로 움직일 수 있나요?"라고 묻고,
        요구조자는 그 단어를 그대로 써서 답한다.

        실측 포함률 0.78이다. 임계값이 0.8이었을 때 여유가 0.02뿐이어서, 걸을 수 있는
        부상자가 무응답으로 집계되고 IMMEDIATE로 올라갔다. 0.9로 올린 근거가 이 값이다.
        """
        answer = "스스로 움직일 수 있어요"
        self.assertFalse(guide_echo_match(answer, GUIDE_BY_TEXT)[0])

        # 임계값을 실측값 아래로 내리면 이 답변이 에코로 판정된다.
        # 이 단정이 깨지면 측정 전제가 바뀐 것이므로 값을 다시 골라야 한다.
        self.assertTrue(
            guide_echo_match(answer, GUIDE_BY_TEXT, ratio=0.75)[0],
            "실측 포함률이 0.78에서 벗어났다. docs/README.md 11-3의 측정표를 갱신할 것",
        )
        self.assertGreaterEqual(
            config.ECHO_MATCH_RATIO,
            0.85,
            "임계값이 실측 최대 0.78과 충분히 떨어져 있어야 한다",
        )

    def test_threshold_keeps_margin_on_both_sides(self):
        """에코와 실제 응답 사이가 비어 있어야 임계값 선택이 정당하다."""
        echoes = [asset.text for asset in GUIDE_ASSETS.values()]
        answers = [
            "스스로 움직일 수 있어요",
            "지금 움직일 수 있어요",
            "숨쉬기 어렵고 피가 나요",
            "두 명이고 움직일 수 없어요",
            "저 말고 대답할 수 있는 사람은 없어요",
        ]
        # 임계값 아래로는 모든 안내 문구가 잡히고, 위로는 어떤 응답도 잡히지 않는다.
        for text in echoes:
            with self.subTest(echo=text[:16]):
                self.assertTrue(guide_echo_match(text, GUIDE_BY_TEXT)[0])
        for text in answers:
            with self.subTest(answer=text):
                self.assertFalse(guide_echo_match(text, GUIDE_BY_TEXT)[0])

    def test_short_text_is_never_echo(self):
        """짧은 응답을 삼키지 않도록 최소 길이를 둔다."""
        is_echo, _ = guide_echo_match("들리면", GUIDE_BY_TEXT)
        self.assertFalse(is_echo)

    def test_empty_and_none_are_safe(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                self.assertEqual(
                    guide_echo_match(value, GUIDE_BY_TEXT), (False, "")
                )

    def test_no_guide_texts_means_no_match(self):
        self.assertEqual(guide_echo_match("아무 말", []), (False, ""))


class EchoBecomesNoResponseTest(unittest.TestCase):
    """165의 본질. 에코를 걸러도 '응답 있음'이면 아무 것도 고친 게 아니다."""

    def test_echo_session_reports_no_response_and_immediate_risk(self):
        intro = GUIDE_ASSETS[GuideCode.INTRO].text
        runner = build_runner(text=intro)
        result = runner.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertIs(
            result.fields["anyResponseDetected"],
            False,
            "에코가 응답으로 집계되면 의식 없는 요구조자를 응답 있음으로 보고한다",
        )
        risk = risk_assessment(result.fields)
        self.assertEqual(risk["riskLevel"], "IMMEDIATE")

    def test_echo_is_classified_as_no_voice_not_stt_failure(self):
        """STT 실패로 두면 anyResponseDetected가 true가 된다."""
        runner = build_runner(text=GUIDE_ASSETS[GuideCode.INTRO].text)
        result = runner.run()
        classes = {turn.response_class for turn in result.turns}
        self.assertEqual(classes, {ResponseClass.NO_VOICE_DETECTED})

    def test_echo_retries_intro_like_real_silence(self):
        runner = build_runner(text=GUIDE_ASSETS[GuideCode.INTRO].text)
        result = runner.run()
        intro = [
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        ]
        self.assertEqual([turn.attempt for turn in intro], [1, 2])

    def test_matched_guide_text_is_kept_for_diagnosis(self):
        intro = GUIDE_ASSETS[GuideCode.INTRO].text
        runner = build_runner(text=intro)
        runner.run()
        echoed = [
            diagnostic
            for diagnostic in runner.diagnostics
            if diagnostic.echo_of is not None
        ]
        self.assertTrue(echoed)
        self.assertEqual(echoed[0].echo_of, intro)
        # STT 원문도 남는다. 판정이 틀렸는지 사람이 대조할 수 있어야 한다.
        self.assertEqual(echoed[0].stt_text, intro)

    def test_real_answer_still_fills_the_report(self):
        """회귀 — 가드가 정상 세션을 막지 않는다."""
        runner = build_runner(text="두 명이고 움직일 수 없어요")
        result = runner.run()
        self.assertIs(result.fields["anyResponseDetected"], True)
        self.assertEqual(result.fields["mobilityStatus"], "NO")
        self.assertEqual(result.fields["reportedResponsiveCount"], 2)


class ListenDelayTest(unittest.TestCase):
    """재생 종료 판정이 실제 가청 종료보다 이르다. 그 차이를 대기로 덮는다."""

    def test_default_delay_is_applied_before_every_listen(self):
        sleeps = []
        runner = build_runner(text="네 들려요", sleeps=sleeps)
        result = runner.run()

        listened = len(result.turns)
        self.assertEqual(len(sleeps), listened)
        self.assertTrue(all(delay == config.LISTEN_DELAY for delay in sleeps))

    def test_default_comes_from_config(self):
        runner = build_runner(text="네 들려요")
        self.assertEqual(runner.listen_delay, config.LISTEN_DELAY)
        self.assertGreaterEqual(config.LISTEN_DELAY, 0.3)

    def test_delay_is_injectable(self):
        sleeps = []
        runner = build_runner(text="네 들려요", sleeps=sleeps, listen_delay=1.5)
        runner.run()
        self.assertTrue(sleeps)
        self.assertTrue(all(delay == 1.5 for delay in sleeps))

    def test_zero_delay_skips_sleeping(self):
        sleeps = []
        runner = build_runner(text="네 들려요", sleeps=sleeps, listen_delay=0)
        runner.run()
        self.assertEqual(sleeps, [])

    def test_delay_happens_before_recording(self):
        """대기가 녹음 뒤에 오면 의미가 없다."""
        order = []

        deps = SessionDependencies(
            record=lambda seconds: (order.append("record"), speech())[1],
            has_speech=lambda wav: True,
            transcribe=lambda wav: ("네 들려요", 0.1),
            extract=lambda value: StubExtraction({"mobilityStatus": "YES"}),
            player=StubPlayer(),
        )
        runner = VoiceSessionRunner(
            deps, sleep=lambda seconds: order.append("sleep")
        )
        runner.run()

        self.assertEqual(order[0], "sleep")
        self.assertEqual(order[1], "record")


if __name__ == "__main__":
    unittest.main()

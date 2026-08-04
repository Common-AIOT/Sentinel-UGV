"""VoiceSessionRunner 배선 검증. 마이크·STT·GMS 없이 같은 경로를 확인한다."""

import unittest

import numpy as np

from sentinel_voice import config
from sentinel_voice.conversation import (
    QuestionCode,
    ResponseClass,
    SessionState,
)
from sentinel_voice.guide_audio import (
    GUIDE_ASSETS,
    GuideCode,
    GuidePlayer,
    PlaybackStatus,
)
from sentinel_voice.session_runner import SessionDependencies, VoiceSessionRunner


class FakeExtraction:
    """`GmsCallResult`와 같은 속성만 노출하는 대역."""

    def __init__(self, extraction, source="GMS"):
        self.extraction = extraction
        self.source = source


class RecordingPlayer(GuidePlayer):
    """실제 재생 없이 어떤 코드가 요청됐는지만 기록한다."""

    def __init__(self):
        self.played = []

    def play_text(self, text, **kwargs):
        from sentinel_voice.guide_audio import GUIDE_BY_TEXT, PlaybackResult

        code = GUIDE_BY_TEXT.get(text)
        self.played.append(code)
        status = (
            PlaybackStatus.PLAYED
            if code is not None
            else PlaybackStatus.UNAPPROVED_TEXT
        )
        return PlaybackResult(code, status, "")


def speech(level=0.2, seconds=1.0):
    """무음 게이트를 통과할 정도의 신호."""
    return np.full(int(config.FS * seconds), level, dtype=np.float32)


SILENCE = np.zeros(config.FS, dtype=np.float32)


def build_runner(
    *,
    audio=None,
    text="네 여기 사람 있어요",
    extraction=None,
    source="GMS",
    record_error=False,
    has_speech=True,
    no_speech_prob=0.1,
):
    player = RecordingPlayer()

    def record(seconds):
        if record_error:
            raise OSError("입력 장치를 열 수 없음")
        return audio if audio is not None else speech()

    def transcribe(wav):
        return text, no_speech_prob

    def extract(value, question=None):
        return FakeExtraction(
            extraction
            if extraction is not None
            else {
                "reportedResponsiveCount": 2,
                "mobilityStatus": "NO",
                "urgentConditionReported": "YES",
            },
            source,
        )

    deps = SessionDependencies(
        record=record,
        has_speech=lambda wav: has_speech,
        transcribe=transcribe,
        extract=extract,
        player=player,
    )
    # 대역 재생기는 스피커 꼬리가 없다. 실제 대기를 넣으면 테스트만 느려진다
    # (기본 300ms × 청취 횟수). 대기 동작 자체는 test_echo_guard가 검증한다.
    return VoiceSessionRunner(deps, listen_delay=0), player


class SessionRunnerTest(unittest.TestCase):
    def test_full_session_fills_report_fields(self):
        """정상 응답이면 5단계가 순서대로 돌고 필드가 채워진다."""
        runner, player = build_runner()
        result = runner.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.termination_reason, "NORMAL")
        self.assertTrue(result.fields["anyResponseDetected"])
        self.assertEqual(result.fields["reportedResponsiveCount"], 2)
        self.assertEqual(result.fields["mobilityStatus"], "NO")
        self.assertEqual(result.fields["urgentConditionReported"], "YES")
        self.assertEqual(
            result.fields["reportedCountStatus"], "SELF_REPORTED_GROUP_COUNT"
        )
        # 승인된 안내 문구만 재생됐다.
        self.assertNotIn(None, player.played)
        self.assertEqual(player.played[0], GuideCode.INTRO)

    def test_stt_failure_is_not_recorded_as_no_response(self):
        """음성은 있으나 STT가 무효면 무응답이 아니라 STT 실패로 분류한다."""
        # 반복 환각을 만들어 is_valid_stt를 실패시킨다.
        runner, _ = build_runner(text="네 네 네 네 네")
        result = runner.run()

        intro_turn = next(
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        )
        self.assertEqual(
            intro_turn.response_class, ResponseClass.VOICE_DETECTED_STT_FAILED
        )
        # 발화는 감지됐으므로 무응답으로 기록하지 않는다.
        self.assertTrue(result.fields["anyResponseDetected"])
        self.assertTrue(result.fields["operatorReviewRequired"])

    def test_silence_is_no_voice_detected(self):
        """무음이면 재질문 후 무응답으로 기록한다."""
        runner, player = build_runner(audio=SILENCE)
        result = runner.run()

        intro_turns = [
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        ]
        self.assertEqual(len(intro_turns), 2)  # INTRO는 1회 재질문
        self.assertEqual(
            intro_turns[0].response_class, ResponseClass.NO_VOICE_DETECTED
        )
        self.assertFalse(result.fields["anyResponseDetected"])
        self.assertIn(GuideCode.RETRY_NO_RESPONSE, player.played)

    def test_audio_device_error_ends_session(self):
        """마이크 오류는 요구조자 상태가 아니라 관찰 실패로 종료한다."""
        runner, _ = build_runner(record_error=True)
        result = runner.run()

        self.assertEqual(result.state, SessionState.FAILED_AUDIO)
        self.assertEqual(result.termination_reason, "AUDIO_DEVICE_ERROR")

    def test_undetermined_value_is_unrecognized(self):
        """UNKNOWN은 확정값이 아니므로 해석 실패로 두고 확인 대상으로 남긴다."""
        runner, _ = build_runner(
            extraction={
                "reportedResponsiveCount": None,
                "mobilityStatus": "UNKNOWN",
                "urgentConditionReported": "UNKNOWN",
            }
        )
        result = runner.run()

        mobility_turn = next(
            turn for turn in result.turns if turn.question == QuestionCode.MOBILITY
        )
        self.assertEqual(
            mobility_turn.response_class, ResponseClass.RESPONSE_UNRECOGNIZED
        )
        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(result.fields["operatorReviewRequired"])

    def test_fallback_source_is_tracked(self):
        """GMS 실패 후 33-8 폴백을 쓴 사실이 세션에 남는다."""
        runner, _ = build_runner(source="FALLBACK")
        runner.run()
        self.assertTrue(runner.used_fallback)

    def test_gms_only_session_reports_no_fallback(self):
        runner, _ = build_runner(source="GMS")
        runner.run()
        self.assertFalse(runner.used_fallback)

    def test_logging_failure_does_not_abort_session(self):
        """로그 출력 실패(콘솔 인코딩 등)가 대화 세션을 중단시키지 않는다."""
        runner, _ = build_runner()

        def exploding_logger(message):
            raise UnicodeEncodeError("cp949", "x", 0, 1, "출력 불가")

        runner._on_event = exploding_logger
        result = runner.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.fields["mobilityStatus"], "NO")

    def test_abort_stops_session(self):
        """안전 중단은 즉시 종료 사유로 기록된다."""
        player = RecordingPlayer()
        deps = SessionDependencies(
            record=lambda seconds: speech(),
            has_speech=lambda wav: True,
            transcribe=lambda wav: ("네", 0.1),
            extract=lambda text, question=None: FakeExtraction({}, "GMS"),
            player=player,
        )
        runner = VoiceSessionRunner(
            deps,
            abort_requested=lambda: SessionState.ABORTED_SAFETY,
            listen_delay=0,
        )
        result = runner.run()

        self.assertEqual(result.state, SessionState.ABORTED_SAFETY)
        self.assertEqual(result.termination_reason, "ABORTED_SAFETY")


if __name__ == "__main__":
    unittest.main()

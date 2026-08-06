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
    GuideCode,
    GuidePlayer,
    PlaybackStatus,
)
from sentinel_voice.remote_asr import RemoteASRError
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


# 조용한 방 — **정확히 0이 아니다.** 살아 있는 마이크는 조용해도 작은 신호를 낸다.
# 0.0038은 2026-08-04 실기에서 조용하다고 판정된 구간의 실측 rms다. SILENCE_RMS
# (0.005) 아래이므로 무음으로 분류되지만 캡처 경로는 살아 있다.
QUIET_ROOM = np.full(config.FS, 0.0038, dtype=np.float32)

# 캡처 경로 사망 — 전 구간이 정확히 0. 마이크가 아니라 빈 입력을 읽고 있는 상태다
# (S15P11A301-257). 무음과 구분해야 한다.
DEAD_INPUT = np.zeros(config.FS, dtype=np.float32)


def build_runner(
    *,
    audio=None,
    text="네 여기 사람 있어요",
    extraction=None,
    source="GMS",
    record_error=False,
    has_speech=True,
    no_speech_prob=0.1,
    transcribe_error=None,
):
    player = RecordingPlayer()

    def record(seconds):
        if record_error:
            raise OSError("입력 장치를 열 수 없음")
        return audio if audio is not None else speech()

    def transcribe(wav):
        if transcribe_error is not None:
            raise transcribe_error
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

    def test_count_keeps_additional_person_report_separate(self):
        """추가 인원 제보는 응답 가능 총인원과 섞지 않고 원문과 함께 보존한다."""
        runner, _ = build_runner(
            text="2층에 우리 아기가 있어요",
            extraction={
                "reportedResponsiveCount": 1,
                "mobilityStatus": "UNKNOWN",
                "urgentConditionReported": "UNKNOWN",
                "additionalPersonReports": [
                    {
                        "subjectText": "우리 아기",
                        "reportedCount": 1,
                        "countStatus": "EXACT",
                        "locationText": "2층",
                        "responseStatus": "UNKNOWN",
                        "certaintyStatus": "ASSERTED",
                        "reportedFloor": 2,
                        "groundingStatus": "UNGROUNDED",
                        "rawUtterance": "2층에 우리 아기가 있어요",
                        "verificationStatus": "UNVERIFIED",
                        "operatorReviewRequired": True,
                    }
                ],
            },
        )
        result = runner.run()

        self.assertEqual(result.fields["reportedResponsiveCount"], 1)
        self.assertEqual(len(result.additional_person_reports), 1)
        report = result.additional_person_reports[0]
        self.assertEqual(report["subjectText"], "우리 아기")
        self.assertEqual(report["reportedFloor"], 2)
        self.assertEqual(report["responseStatus"], "UNKNOWN")

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

    def test_remote_asr_failure_is_not_recorded_as_no_response(self):
        """GPU 서버 실패도 사람의 무응답으로 바뀌면 안 된다."""
        runner, _ = build_runner(
            transcribe_error=RemoteASRError("ASR_TIMEOUT", retryable=True)
        )
        result = runner.run()

        intro_turn = next(
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        )
        self.assertEqual(
            intro_turn.response_class, ResponseClass.VOICE_DETECTED_STT_FAILED
        )
        self.assertTrue(result.fields["anyResponseDetected"])
        self.assertTrue(result.fields["operatorReviewRequired"])
        self.assertEqual(runner.diagnostics[0].stt_invalid_reason, "ASR_TIMEOUT")

    def test_silence_is_no_voice_detected(self):
        """무음이면 재질문 후 무응답으로 기록한다."""
        runner, player = build_runner(audio=QUIET_ROOM)
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

    def test_dead_input_is_device_error_not_no_response(self):
        """입력이 디지털 무음이면 장치 오류다. 요구조자 무응답으로 보고하지 않는다.

        2026-08-04 젯슨에서 PulseAudio 기본 소스가 빈 아날로그 단자로 잡혀 있어
        리허설 영상 295초가 전부 peak 0이었다(S15P11A301-257). 그대로면 마이크
        사망이 `anyResponseDetected=false` → `IMMEDIATE`로 보고된다. README 10-3
        치명 오류 목록의 "시스템 장애를 요구조자 무응답으로 변환"이다.
        """
        runner, _ = build_runner(audio=DEAD_INPUT)
        result = runner.run()

        self.assertEqual(result.state, SessionState.FAILED_AUDIO)
        self.assertEqual(result.termination_reason, "AUDIO_DEVICE_ERROR")
        # 무응답으로 단정하지 않았다. false가 새어 들어가면 위 오류가 되살아난다.
        self.assertNotEqual(result.fields.get("anyResponseDetected"), False)

    def test_quiet_room_is_still_no_response(self):
        """무음 감지가 진짜 무응답 경로를 삼켜서는 안 된다.

        조용한 방(실측 rms 0.0038)은 여전히 무응답이다. 이 값과 디지털 무음
        임계값(1e-6) 사이는 세 자리 이상 벌어져 있다.
        """
        runner, _ = build_runner(audio=QUIET_ROOM)
        result = runner.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertFalse(result.fields["anyResponseDetected"])
        intro_turns = [
            turn for turn in result.turns if turn.question == QuestionCode.INTRO
        ]
        self.assertEqual(
            intro_turns[0].response_class, ResponseClass.NO_VOICE_DETECTED
        )

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

    def test_mobility_yes_with_negation_is_downgraded_to_unknown(self):
        """ASR 부정어 손실이 의심되는 모순 전사를 안전한 값으로 낮춘다."""
        runner, _ = build_runner(
            text="아니요. 다리 다쳐서 움직입니다.",
            extraction={
                "reportedResponsiveCount": None,
                "mobilityStatus": "YES",
                "urgentConditionReported": "UNKNOWN",
            },
        )
        result = runner.run()

        self.assertEqual(result.fields["mobilityStatus"], "UNKNOWN")
        self.assertTrue(result.fields["operatorReviewRequired"])
        mobility = next(
            item
            for item in runner.diagnostics
            if item.question == QuestionCode.MOBILITY
        )
        self.assertEqual(
            mobility.safety_reason, "MOBILITY_YES_NEGATION_CONFLICT"
        )

    def test_clear_mobility_yes_is_preserved(self):
        runner, _ = build_runner(
            text="네, 걸어서 이동할 수 있어요.",
            extraction={"mobilityStatus": "YES"},
        )
        result = runner.run()

        self.assertEqual(result.fields["mobilityStatus"], "YES")

    def test_rhetorical_mobility_negation_is_no(self):
        """실기에서 확인된 한국어 반문은 이동 불가로 확정한다."""
        runner, _ = build_runner(
            text="다리 다쳤는데 움직일 수 있겠냐고요.",
            extraction={"mobilityStatus": "UNKNOWN"},
        )
        result = runner.run()

        self.assertEqual(result.fields["mobilityStatus"], "NO")
        mobility = next(
            item for item in runner.diagnostics
            if item.question == QuestionCode.MOBILITY
        )
        self.assertEqual(
            mobility.safety_reason, "MOBILITY_RHETORICAL_NEGATION"
        )

    def test_tentative_positive_is_not_forced_to_no(self):
        runner, _ = build_runner(
            text="조금 쉬면 움직일 수 있겠어요.",
            extraction={"mobilityStatus": "YES"},
        )
        result = runner.run()

        self.assertEqual(result.fields["mobilityStatus"], "YES")

    def test_turn_diagnostics_capture_stage_timings(self):
        runner, _ = build_runner()
        runner.run()

        for diagnostic in runner.diagnostics:
            self.assertIsNotNone(diagnostic.record_ms)
            self.assertIsNotNone(diagnostic.vad_ms)
            self.assertIsNotNone(diagnostic.stt_ms)
            self.assertIsNotNone(diagnostic.turn_ms)
            if diagnostic.question != QuestionCode.INTRO:
                self.assertIsNotNone(diagnostic.gms_ms)

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

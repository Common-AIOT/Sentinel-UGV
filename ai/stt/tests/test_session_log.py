"""대화 세션 기록 저장 (S15P11A301-178).

시나리오 C가 3차까지 재현되지 않았는데 마이크에 무엇이 들어왔는지 확인할 방법이
없어 코드 결함인지 잡음인지 판정하지 못했다. 그 계측을 검증한다.

핵심은 세 가지다.
  - 저장 위치를 지정하지 않으면 아무 것도 쓰지 않는다(개인정보 기본 비활성).
  - INTRO 재질문의 1차 청취가 2차에 덮이지 않는다(에코 진단의 전제).
  - 저장 실패가 대화 세션을 중단시키지 않는다(계측이 임무를 멈추면 안 된다).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from sentinel_voice import config
from sentinel_voice.conversation import ASKED_QUESTIONS, QuestionCode, SessionState
from sentinel_voice.guide_audio import GUIDE_BY_TEXT, PlaybackResult, PlaybackStatus
from sentinel_voice.session_log import ENV_DIR, SessionLog, open_session_log
from sentinel_voice.session_runner import SessionDependencies, VoiceSessionRunner

# 쓸 수 없는 경로. 드라이브가 없어야 mkdir와 open이 모두 실패한다.
UNWRITABLE = Path("Z:/sentinel-없는드라이브/sessions")


class StubPlayer:
    def play_text(self, text, **kwargs):
        code = GUIDE_BY_TEXT.get(text)
        return PlaybackResult(code, PlaybackStatus.PLAYED, "")


def speech(level=0.2, seconds=1.0):
    """무음 게이트를 통과할 정도의 신호."""
    return np.full(int(config.FS * seconds), level, dtype=np.float32)


def silence():
    return np.zeros(config.FS, dtype=np.float32)


class StubExtraction:
    def __init__(self, extraction, source="GMS"):
        self.extraction = extraction
        self.source = source


def build_runner(session_log, *, audio=None, has_speech=True, text="네"):
    deps = SessionDependencies(
        record=lambda seconds: speech() if audio is None else audio,
        has_speech=lambda wav: has_speech,
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
    # 대역 재생기는 스피커 꼬리가 없다. 실제 대기는 테스트만 느리게 한다(165).
    return VoiceSessionRunner(deps, session_log=session_log, listen_delay=0)


def read_records(directory):
    lines = (directory / "session.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def turn_records(directory):
    return [
        record for record in read_records(directory) if record["type"] == "turn"
    ]


def listened(directory):
    """청취가 있었던 기록만."""
    return [
        record
        for record in turn_records(directory)
        if record["responseClass"] is not None
    ]


class TempSessionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = open_session_log(temp.name)
        self.assertTrue(self.log.enabled)


class DisabledByDefaultTest(unittest.TestCase):
    """개인정보이므로 명시적으로 켜지 않으면 저장하지 않는다."""

    def setUp(self):
        self.saved = os.environ.pop(ENV_DIR, None)
        if self.saved is not None:
            self.addCleanup(os.environ.__setitem__, ENV_DIR, self.saved)

    def test_env_unset_means_disabled(self):
        log = open_session_log()
        self.assertFalse(log.enabled)
        self.assertIsNone(log.directory)

    def test_empty_env_value_means_disabled(self):
        os.environ[ENV_DIR] = ""
        self.addCleanup(os.environ.pop, ENV_DIR, None)
        self.assertFalse(open_session_log().enabled)

    def test_env_value_enables_saving(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        os.environ[ENV_DIR] = temp.name
        self.addCleanup(os.environ.pop, ENV_DIR, None)

        log = open_session_log()
        self.assertTrue(log.enabled)
        self.assertTrue(log.directory.is_dir())
        self.assertEqual(log.directory.parent, Path(temp.name))

    def test_disabled_log_accepts_every_call(self):
        """호출부가 분기하지 않도록 비활성 로그도 같은 인터페이스를 가진다."""
        log = SessionLog(None)
        log.start(source="VISION", timeout_seconds=120)
        log.turn({"question": "INTRO"})
        log.finish({"state": "COMPLETED"})
        log.report({"a": 1})
        self.assertIsNone(log.audio("INTRO", 1, 1, speech()))

    def test_session_runner_defaults_to_disabled(self):
        runner = build_runner(None)
        self.assertFalse(runner.session_log.enabled)

    def test_disabled_session_writes_no_file(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        runner = build_runner(None)
        runner.run()
        self.assertEqual(list(Path(temp.name).iterdir()), [])


class SessionTranscriptTest(TempSessionTest):
    def test_start_line_records_values_needed_to_reproduce(self):
        """정규화 결과와 무음 판정을 재현하려면 그때 쓰인 기준값이 필요하다."""
        self.log.start(source="VISION", timeout_seconds=180)
        start = read_records(self.log.directory)[0]
        self.assertEqual(start["type"], "session_start")
        self.assertEqual(start["normTargetRms"], config.NORM_TARGET_RMS)
        self.assertEqual(start["silenceRms"], config.SILENCE_RMS)
        self.assertEqual(start["sampleRate"], config.FS)
        self.assertEqual(start["timeoutSeconds"], 180)

    def test_full_session_writes_turns_and_end(self):
        runner = build_runner(self.log)
        result = runner.run()

        records = read_records(self.log.directory)
        self.assertEqual(records[0]["type"], "session_start")
        self.assertEqual(records[-1]["type"], "session_end")

        end = records[-1]
        self.assertEqual(end["state"], result.state.value)
        self.assertEqual(end["terminationReason"], "NORMAL")
        self.assertEqual(end["fields"]["mobilityStatus"], "NO")

        asked = [turn["question"] for turn in turn_records(self.log.directory)]
        self.assertEqual(asked, [code.value for code in ASKED_QUESTIONS])
        # 종료 안내는 상태머신 밖에서 일어나므로 턴 기록에 없다.
        self.assertNotIn(QuestionCode.CLOSING.value, asked)

    def test_closing_announcement_is_recorded_separately(self):
        """종료 안내가 실제로 무엇을 말했는지 남아야 한다(183 검증 근거).

        상태머신이 아니라 전송 단계가 안내하므로 턴이 아니라 별도 기록이다.
        """
        self.log.announcement("REPORT_SUCCEEDED_DEPARTURE", "PLAYED", "ACK 대기")
        records = [
            record
            for record in read_records(self.log.directory)
            if record["type"] == "announcement"
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["guide"], "REPORT_SUCCEEDED_DEPARTURE")
        self.assertEqual(records[0]["status"], "PLAYED")

    def test_turn_keeps_full_extraction_not_only_the_asked_field(self):
        """질문이 요구한 필드 외 추출값은 보고 스키마에 담을 자리가 없다(11-1)."""
        runner = build_runner(self.log)
        runner.run()
        turns = [
            record
            for record in turn_records(self.log.directory)
            if record["question"] == QuestionCode.COUNT.value
        ]
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["extraction"]["urgentConditionReported"], "YES")

    def test_raw_audio_is_saved_before_normalization(self):
        """정규화 후만 남기면 큰 목소리와 증폭된 에코를 구분할 수 없다."""
        level = 0.02
        runner = build_runner(self.log, audio=speech(level=level))
        runner.run()

        files = sorted(path.name for path in self.log.directory.glob("*.wav"))
        self.assertTrue(files, "청취 원본이 저장되지 않았다")

        import soundfile

        saved, rate = soundfile.read(str(self.log.directory / files[0]))
        self.assertEqual(rate, config.FS)
        actual = float(np.sqrt(np.mean(np.square(saved))))
        self.assertAlmostEqual(actual, level, places=3)
        # 정규화되었다면 NORM_TARGET_RMS(0.08)에 가까워진다.
        self.assertLess(actual, config.NORM_TARGET_RMS / 2)

    def test_silent_turn_is_still_recorded(self):
        """무음으로 빠지는 경로가 진단 대상이다. 여기서 저장을 건너뛰면 안 된다."""
        runner = build_runner(self.log, audio=silence())
        runner.run()

        turns = listened(self.log.directory)
        self.assertTrue(turns)
        self.assertTrue(all(turn["audio"] for turn in turns))
        self.assertTrue(
            any(turn["responseClass"] == "NO_VOICE_DETECTED" for turn in turns)
        )

    def test_vad_miss_is_recorded_with_raw_level(self):
        """D 시나리오 진단용 — VAD가 놓쳤을 때 원본 음량이 남아야 한다."""
        runner = build_runner(self.log, has_speech=False)
        runner.run()
        turns = listened(self.log.directory)
        self.assertTrue(turns)
        self.assertTrue(all(turn["rawRms"] > config.SILENCE_RMS for turn in turns))
        self.assertTrue(all(turn["audio"] for turn in turns))

    def test_report_is_written_separately(self):
        self.log.report({"report": {"responseScope": "GROUP"}})
        payload = json.loads(
            (self.log.directory / "report.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["report"]["responseScope"], "GROUP")


class RetryAttemptIsPreservedTest(TempSessionTest):
    """INTRO 재질문의 1차 관찰이 2차에 덮이면 에코 가설(165)을 검증할 수 없다."""

    def test_both_intro_attempts_are_kept(self):
        runner = build_runner(self.log, audio=silence())
        runner.run()

        intro = [
            diagnostic
            for diagnostic in runner.diagnostics
            if diagnostic.question == QuestionCode.INTRO
        ]
        self.assertEqual([diagnostic.attempt for diagnostic in intro], [1, 2])

        turns = [
            record
            for record in turn_records(self.log.directory)
            if record["question"] == QuestionCode.INTRO.value
        ]
        self.assertEqual([turn["attempt"] for turn in turns], [1, 2])
        self.assertEqual(
            len({turn["audio"] for turn in turns}),
            2,
            "두 시도의 오디오가 같은 파일이면 1차가 덮인 것이다",
        )

    def test_audio_filenames_carry_question_and_attempt(self):
        runner = build_runner(self.log, audio=silence())
        runner.run()
        names = sorted(path.name for path in self.log.directory.glob("*.wav"))
        self.assertIn("turn_01_INTRO_a1.wav", names)
        self.assertIn("turn_02_INTRO_a2.wav", names)


class LoggingNeverBreaksTheSessionTest(unittest.TestCase):
    """계측이 임무를 멈추면 안 된다. 저장이 실패해도 세션은 끝까지 간다."""

    def test_unwritable_root_degrades_to_disabled(self):
        log = open_session_log(UNWRITABLE)
        self.assertFalse(log.enabled)

    def test_write_failures_are_reported_but_swallowed(self):
        warnings = []
        log = SessionLog(UNWRITABLE, warnings.append)
        log.start(source="VISION", timeout_seconds=120)
        log.turn({"question": "INTRO"})
        log.report({"a": 1})
        self.assertIsNone(log.audio("INTRO", 1, 1, speech()))
        self.assertTrue(warnings, "조용히 실패하면 저장이 안 되는 걸 모른다")

    def test_session_completes_when_every_log_call_raises(self):
        events = []

        class ExplodingLog(SessionLog):
            def start(self, **kwargs):
                raise RuntimeError("디스크 오류")

            def audio(self, *args, **kwargs):
                raise OSError("디스크 가득 참")

            def turn(self, record):
                raise RuntimeError("쓰기 실패")

            def finish(self, record):
                raise RuntimeError("쓰기 실패")

        runner = build_runner(ExplodingLog(UNWRITABLE))
        runner._on_event = events.append
        result = runner.run()

        self.assertEqual(result.state, SessionState.COMPLETED)
        self.assertEqual(result.termination_reason, "NORMAL")
        self.assertEqual(result.fields["mobilityStatus"], "NO")
        self.assertTrue(
            any("[LOG]" in message for message in events),
            "실패를 알리지 않으면 저장이 비어 있는 걸 모른다",
        )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from sentinel_voice import config
from sentinel_voice.guide_audio import (
    GUIDE_ASSETS,
    GuideCode,
    GuidePlayer,
    PlaybackStatus,
    validate_wav,
)
from tools.validate_guide_assets import validate_assets
from tools.convert_guide_assets import source_filename


class FakeBackend:
    def __init__(self, error=None):
        self.error = error
        self.play_calls = []
        self.wait_calls = 0

    def play(self, samples, sample_rate):
        if self.error:
            raise self.error
        self.play_calls.append((samples, sample_rate))

    def wait(self):
        self.wait_calls += 1


def write_test_wav(path: Path, *, channels=1, sample_rate=config.FS):
    seconds = 0.5
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    samples = (0.12 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    if channels == 2:
        samples = np.column_stack([samples, samples]).reshape(-1)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())


class GuideAudioTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.assets = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def asset_path(self, code):
        return self.assets / GUIDE_ASSETS[code].filename

    def test_valid_pcm16_mono_16k_wav_is_played(self):
        path = self.asset_path(GuideCode.INTRO)
        write_test_wav(path)
        backend = FakeBackend()

        result = GuidePlayer(backend, self.assets).play(GuideCode.INTRO)

        self.assertEqual(result.status, PlaybackStatus.PLAYED)
        self.assertEqual(len(backend.play_calls), 1)
        self.assertEqual(backend.play_calls[0][1], config.FS)
        self.assertEqual(backend.wait_calls, 1)
        self.assertEqual(validate_wav(path).channels, 1)

    def test_missing_asset_is_reported_without_dynamic_tts(self):
        backend = FakeBackend()
        result = GuidePlayer(backend, self.assets).play(GuideCode.INTRO)

        self.assertEqual(result.status, PlaybackStatus.ASSET_NOT_FOUND)
        self.assertEqual(backend.play_calls, [])

    def test_invalid_stereo_asset_is_rejected(self):
        write_test_wav(self.asset_path(GuideCode.INTRO), channels=2)
        result = GuidePlayer(FakeBackend(), self.assets).play(GuideCode.INTRO)

        self.assertEqual(result.status, PlaybackStatus.INVALID_ASSET)
        self.assertIn("mono", result.detail)

    def test_device_error_is_not_reported_as_missing_asset(self):
        write_test_wav(self.asset_path(GuideCode.INTRO))
        result = GuidePlayer(
            FakeBackend(RuntimeError("speaker unavailable")), self.assets
        ).play(GuideCode.INTRO)

        self.assertEqual(result.status, PlaybackStatus.DEVICE_ERROR)

    def test_success_message_requires_report_confirmation(self):
        write_test_wav(self.asset_path(GuideCode.REPORT_SUCCEEDED))
        player = GuidePlayer(FakeBackend(), self.assets)

        blocked = player.play(GuideCode.REPORT_SUCCEEDED)
        allowed = player.play(
            GuideCode.REPORT_SUCCEEDED, report_succeeded=True
        )

        self.assertEqual(
            blocked.status, PlaybackStatus.REPORT_NOT_CONFIRMED
        )
        self.assertEqual(allowed.status, PlaybackStatus.PLAYED)

    def test_departure_message_requires_resume_only(self):
        """S15P11A301-183: 완료+탐사 안내는 발신 완료만으로 재생한다.

        관제 ACK 잠금(requires_report_success)을 떼고 탐사 재개 조건만 남겼다.
        근거와 수용한 위험은 docs/README.md 2-6에 있다.
        """
        write_test_wav(
            self.asset_path(GuideCode.REPORT_SUCCEEDED_DEPARTURE)
        )
        player = GuidePlayer(FakeBackend(), self.assets)

        resume_blocked = player.play(GuideCode.REPORT_SUCCEEDED_DEPARTURE)
        allowed_without_ack = player.play(
            GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            exploration_resume_approved=True,
        )

        self.assertEqual(
            resume_blocked.status,
            PlaybackStatus.EXPLORATION_RESUME_NOT_APPROVED,
        )
        self.assertEqual(allowed_without_ack.status, PlaybackStatus.PLAYED)

    def test_unapproved_free_text_is_rejected(self):
        result = GuidePlayer(FakeBackend(), self.assets).play_text(
            "곧 구조대가 반드시 도착합니다."
        )
        self.assertEqual(result.status, PlaybackStatus.UNAPPROVED_TEXT)

    def test_batch_validation_keeps_missing_assets_as_result_rows(self):
        write_test_wav(self.asset_path(GuideCode.INTRO))

        rows, failures = validate_assets(self.assets)

        self.assertEqual(len(rows), len(GUIDE_ASSETS))
        self.assertEqual(failures, len(GUIDE_ASSETS) - 1)
        intro = next(row for row in rows if row["code"] == "INTRO")
        missing = next(
            row for row in rows if row["code"] == "ASK_COUNT"
        )
        self.assertEqual(intro["status"], "OK")
        self.assertIn("rms_dbfs", intro)
        self.assertEqual(missing["status"], "FAIL")
        self.assertEqual(missing["error"], "FILE_NOT_FOUND")

    def test_minimax_source_names_match_every_guide_asset(self):
        actual = {
            code: source_filename(asset.filename)
            for code, asset in GUIDE_ASSETS.items()
        }

        self.assertEqual(actual[GuideCode.INTRO], "mini_intro.wav")
        self.assertEqual(
            actual[GuideCode.REPORT_SUCCEEDED_DEPARTURE],
            "mini_report_succeeded_departure.wav",
        )
        # 개수를 고정하지 않는다. 안내를 추가할 때마다 이 테스트가 깨지면
        # 실제 검증(코드마다 원본 파일명이 하나씩 대응)의 의미가 흐려진다.
        self.assertEqual(len(actual), len(GuideCode))
        self.assertEqual(len(set(actual.values())), len(GuideCode))


if __name__ == "__main__":
    unittest.main()

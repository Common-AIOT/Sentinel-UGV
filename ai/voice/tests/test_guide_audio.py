import re
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

    def test_departure_message_requires_resume_only(self):
        """종료 안내는 탐사 재개 조건만 요구한다.

        관제 ACK 잠금(requires_report_success)은 ACK 부재 확정(2026-08-01,
        로봇 다수 투입)으로 필드째 제거됐다. 재개 게이트는 유지 — E-Stop
        상태에서 "다시 탐색을 시작합니다"를 말하면 거짓이 된다.
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

    def test_committed_assets_are_not_faster_than_conversation(self):
        """안내가 일상 대화보다 빠르면 안 된다 (S15P11A301-260).

        규격 검사(`validate_wav`)는 길이·레벨만 본다. "너무 빠르다"는 그것으로
        드러나지 않아서 2026-08-04까지 최고 8.2 음절/초로 나가고 있었다. 원인은
        MiniMax Speed 1.1이었고, 변환에서 `atempo=0.8`로 늘려 6.7 이하로 내렸다.

        상한 7.0은 일상 대화 속도(5~7 음절/초)의 위쪽이다. 이 테스트가 깨지면
        자산이 원본 속도로 되돌아갔다는 뜻이다 — 배율을 빼고 재변환한 경우다.
        """
        hangul = re.compile(r"[가-힣]")
        assets_dir = config.VOICE_ROOT / "assets"
        rates = {}
        for code, asset in GUIDE_ASSETS.items():
            path = assets_dir / asset.filename
            if not path.is_file():  # 자산 미배치 환경에서는 검증 대상이 아니다
                self.skipTest(f"자산 없음: {path}")
            inspection = validate_wav(path)
            syllables = len(hangul.findall(asset.text))
            rates[code.value] = syllables / inspection.duration_seconds

        too_fast = {code: round(r, 1) for code, r in rates.items() if r > 7.0}
        self.assertEqual(
            too_fast,
            {},
            f"7.0 음절/초를 넘는 안내가 있다: {too_fast} — "
            "사전 녹음 WAV의 재생 속도를 확인하라",
        )


if __name__ == "__main__":
    unittest.main()

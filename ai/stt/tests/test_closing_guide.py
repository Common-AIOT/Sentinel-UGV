"""세션 종료 안내의 선택 규칙 (S15P11A301-183).

관제 ACK 어댑터가 없는 상태에서 시연용 완료형 안내를 쓰기 위해, 기존 완료+탐사
자산(`REPORT_SUCCEEDED_DEPARTURE`)의 `requires_report_success` 잠금을 풀어 발신
완료(QUEUED) 시점에 재생한다. 새 문구를 녹음하지 않고 자산을 재사용한다.

  발신 진행 중 · 재개 불가   REPORT_PENDING              잠금 없음
  발신 완료 + 임무 진행 중    REPORT_SUCCEEDED_DEPARTURE  requires_exploration_resume
  관제 ACK 확인 · 재개 없음   REPORT_SUCCEEDED            requires_report_success (S15P11A301-182)
"""

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
)
from sentinel_voice.integration import mission_resume_expected
from sentinel_voice.report_delivery import DeliveryState, queue_report


class FakeBackend:
    def __init__(self):
        self.play_calls = []

    def play(self, samples, sample_rate):
        self.play_calls.append((samples, sample_rate))

    def wait(self):
        pass


def write_playable_wav(path: Path) -> None:
    """validate_wav를 통과하는 최소 WAV. 실물 승인 자산과 무관하다."""
    seconds = 0.5
    t = np.arange(int(config.FS * seconds)) / config.FS
    samples = (0.12 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(config.FS)
        wav.writeframes(samples.tobytes())


class SessionClosingGuideTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.assets = Path(self.temp.name)
        for asset in GUIDE_ASSETS.values():
            write_playable_wav(self.assets / asset.filename)
        self.player = GuidePlayer(FakeBackend(), self.assets)
        self.addCleanup(self.temp.cleanup)

    def test_departure_guide_plays_without_ack(self):
        """발신 완료만으로 완료+탐사 안내를 재생할 수 있다."""
        result = self.player.play(
            GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            exploration_resume_approved=True,
        )
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.status, PlaybackStatus.PLAYED)

    def test_departure_guide_requires_resume_expectation(self):
        """재개를 기대할 수 없으면 탐사 문구를 재생하지 않는다."""
        blocked = self.player.play(GuideCode.REPORT_SUCCEEDED_DEPARTURE)
        self.assertFalse(blocked.ok)
        self.assertEqual(
            blocked.status, PlaybackStatus.EXPLORATION_RESUME_NOT_APPROVED
        )

    def test_standalone_success_guide_stays_locked(self):
        """회귀 방지: 탐사 문구가 없는 단독 완료 안내는 여전히 ACK를 요구한다."""
        result = self.player.play(GuideCode.REPORT_SUCCEEDED)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, PlaybackStatus.REPORT_NOT_CONFIRMED)

    def test_no_asset_claims_the_location_is_safe(self):
        """로봇은 요구조자 지점의 안전을 판단할 수 없다(2026-07-30 결정)."""
        for code, asset in GUIDE_ASSETS.items():
            with self.subTest(code=code):
                self.assertNotIn("안전하게", asset.text)

    def test_no_asset_claims_absolute_position_was_sent(self):
        """상대 좌표만 가진 로봇은 절대 위치 전달을 안내하지 않는다."""
        for code, asset in GUIDE_ASSETS.items():
            with self.subTest(code=code):
                self.assertNotIn("위치가 관제", asset.text)


class DocumentedTextMatchesCodeTest(unittest.TestCase):
    """문서 6-1 표의 문구와 코드가 어긋나면 UNAPPROVED_TEXT로 재생이 거부된다.

    사람이 문서만 고치거나 코드만 고치는 실수를 CI 없이도 잡기 위한 테스트다.
    """

    def setUp(self):
        doc = config.STT_ROOT / "docs" / "README.md"
        self.lines = doc.read_text(encoding="utf-8").splitlines()

    def test_every_asset_text_appears_in_docs(self):
        for code, asset in GUIDE_ASSETS.items():
            with self.subTest(code=code):
                row = [
                    line
                    for line in self.lines
                    if asset.filename in line and asset.text in line
                ]
                self.assertTrue(
                    row,
                    f"{code.value}의 파일명과 문구가 같은 표 행에 없다. "
                    "docs/README.md 6-1 표를 코드와 함께 갱신한다.",
                )

    def test_no_asset_needs_a_new_recording(self):
        """모든 승인 자산에 실물 WAV가 있어야 한다.

        S15P11A301-183은 새 녹음 없이 기존 자산만 재사용하기로 했다.
        """
        assets_dir = config.STT_ROOT / "assets"
        for code, asset in GUIDE_ASSETS.items():
            with self.subTest(code=code):
                self.assertTrue(
                    (assets_dir / asset.filename).is_file(),
                    f"{code.value}의 WAV가 없다: {asset.filename}",
                )


class DeliveryGuideSelectionTest(unittest.TestCase):
    def test_pending_adapter_keeps_progressive_form(self):
        """전송 어댑터가 없으면 완료형을 쓰지 않는다."""
        result = queue_report({"sessionId": "s1"})
        self.assertEqual(result.state, DeliveryState.PENDING)
        self.assertEqual(result.guide_code, GuideCode.REPORT_PENDING)

    def test_accepted_handoff_uses_departure_form(self):
        result = queue_report({"sessionId": "s1"}, lambda _report: True)
        self.assertEqual(result.state, DeliveryState.QUEUED)
        self.assertEqual(
            result.guide_code, GuideCode.REPORT_SUCCEEDED_DEPARTURE
        )

    def test_failed_handoff_uses_network_wait(self):
        result = queue_report({"sessionId": "s1"}, lambda _report: False)
        self.assertEqual(result.state, DeliveryState.FAILED)
        self.assertEqual(result.guide_code, GuideCode.NETWORK_WAIT)


class MissionResumeExpectationTest(unittest.TestCase):
    """즉시 재개 정책에서 탐사 문구를 약속해도 되는 상태인지."""

    def test_active_mission_states_expect_resume(self):
        for state in (
            "EXPLORING",
            "PERSON_APPROACHING",
            "INTERACTING",
            "POST_RECORDING",
            "REPORTING",
        ):
            with self.subTest(state=state):
                self.assertTrue(mission_resume_expected(state))

    def test_stopped_or_finished_states_do_not_promise_resume(self):
        """중단·정지·종료 상태에서 "탐사를 계속하겠습니다"를 말하면 거짓이 된다."""
        for state in (
            "ESTOP",
            "ERROR",
            "PAUSED",
            "MANUAL",
            "SAFE_IDLE",
            "COMPLETED",
            "RETURNING",
        ):
            with self.subTest(state=state):
                self.assertFalse(mission_resume_expected(state))

    def test_unknown_state_does_not_promise_resume(self):
        """상태를 한 번도 받지 못했으면 약속하지 않는다."""
        for state in (None, "", "NOT_A_STATE", 0, [], {}):
            with self.subTest(state=state):
                self.assertFalse(mission_resume_expected(state))


if __name__ == "__main__":
    unittest.main()

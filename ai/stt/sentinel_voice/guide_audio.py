"""승인된 사전녹음 안내 음성의 목록, 형식 검증, 안전 재생."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
import wave

import numpy as np

from . import config


class GuideCode(str, Enum):
    """안내 문구의 안정적인 식별자. 파일명이나 문장 자체를 이벤트로 사용하지 않는다.

    2026-08-01 문구 v2(S15P11A301-146)에서 6개로 축소했다. RETRY_UNCLEAR·
    REPORT_PENDING·REPORT_SUCCEEDED·NETWORK_WAIT는 삭제 — 재질문 축소(201),
    종료 안내 단일화, 관제 ACK 부재 확정에 따른다. 경위는 Jira 146·198.
    """

    INTRO = "INTRO"
    ASK_COUNT = "ASK_COUNT"
    ASK_MOBILITY = "ASK_MOBILITY"
    ASK_URGENT = "ASK_URGENT"
    RETRY_NO_RESPONSE = "RETRY_NO_RESPONSE"
    REPORT_SUCCEEDED_DEPARTURE = "REPORT_SUCCEEDED_DEPARTURE"


@dataclass(frozen=True)
class GuideAsset:
    code: GuideCode
    filename: str
    text: str
    requires_exploration_resume: bool = False


# 문구 v2 (2026-08-01, S15P11A301-146 확정). 합니다체 통일, "관제" 금지("구조대"),
# 공백 제외 8자 이상(에코 가드 하한). 문서 §6-1 표와 한 글자도 달라선 안 된다.
GUIDE_ASSETS: dict[GuideCode, GuideAsset] = {
    GuideCode.INTRO: GuideAsset(
        GuideCode.INTRO,
        "guide_intro.wav",
        "구조 로봇입니다. 들리면 대답해 주세요.",
    ),
    GuideCode.ASK_COUNT: GuideAsset(
        GuideCode.ASK_COUNT,
        "guide_ask_count.wav",
        "주변에 다른 인원이 있습니까?",
    ),
    # 공백 제외 8자로 에코 가드 하한(`ECHO_MIN_CHARS=8`)에 정확히 걸린다. 온전히
    # 들리면 잡히지만 꼬리 조각은 잡지 못한다 — 1차 방어선은 `LISTEN_DELAY`다.
    GuideCode.ASK_MOBILITY: GuideAsset(
        GuideCode.ASK_MOBILITY,
        "guide_ask_mobility.wav",
        "움직일 수 있습니까?",
    ),
    GuideCode.ASK_URGENT: GuideAsset(
        GuideCode.ASK_URGENT,
        "guide_ask_urgent.wav",
        "다친 곳이 있으십니까?",
    ),
    GuideCode.RETRY_NO_RESPONSE: GuideAsset(
        GuideCode.RETRY_NO_RESPONSE,
        "guide_retry_no_response.wav",
        "제 말이 들린다면 대답해주십시오.",
    ),
    # 유일한 종료 안내(발신 상태 무관 — 실패 없음 가정, Jira 201).
    # "전달되었습니다"는 발신 성공 근거이며 관제 ACK가 아니다. 탐사 재개를
    # 약속할 수 없는 상태에서는 호출자가 재생을 생략한다(ros_node).
    GuideCode.REPORT_SUCCEEDED_DEPARTURE: GuideAsset(
        GuideCode.REPORT_SUCCEEDED_DEPARTURE,
        "guide_report_succeeded_departure.wav",
        "구조 요청이 전달되었습니다. 다시 탐색을 시작합니다.",
        requires_exploration_resume=True,
    ),
}

GUIDE_BY_TEXT = {asset.text: code for code, asset in GUIDE_ASSETS.items()}


class PlaybackStatus(str, Enum):
    PLAYED = "PLAYED"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    INVALID_ASSET = "INVALID_ASSET"
    DEVICE_ERROR = "DEVICE_ERROR"
    EXPLORATION_RESUME_NOT_APPROVED = "EXPLORATION_RESUME_NOT_APPROVED"
    UNAPPROVED_TEXT = "UNAPPROVED_TEXT"


@dataclass(frozen=True)
class PlaybackResult:
    code: GuideCode | None
    status: PlaybackStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PlaybackStatus.PLAYED


@dataclass(frozen=True)
class WavInspection:
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float
    peak_dbfs: float
    rms_dbfs: float


def inspect_wav(path: Path) -> WavInspection:
    """운영 WAV의 형식과 기본 레벨을 검사한다."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        compression = wav.getcomptype()
        frames = wav.readframes(frame_count)

    if compression != "NONE":
        raise ValueError(f"압축 WAV는 지원하지 않음: {compression}")
    if channels != 1:
        raise ValueError(f"mono가 아님: {channels} channels")
    if sample_width != 2:
        raise ValueError(f"PCM 16-bit가 아님: {sample_width * 8} bit")
    if sample_rate != config.FS:
        raise ValueError(f"샘플레이트가 {config.FS}Hz가 아님: {sample_rate}Hz")
    if frame_count <= 0:
        raise ValueError("오디오 프레임이 없음")

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(np.square(samples))))
    if peak <= 0 or rms <= 0:
        raise ValueError("완전 무음 파일")

    def dbfs(value: float) -> float:
        return float(20 * np.log10(max(value, 1e-12)))

    return WavInspection(
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_seconds=frame_count / sample_rate,
        peak_dbfs=dbfs(peak),
        rms_dbfs=dbfs(rms),
    )


def validate_wav(path: Path) -> WavInspection:
    inspection = inspect_wav(path)
    if not 0.3 <= inspection.duration_seconds <= 15.0:
        raise ValueError(
            f"길이 범위(0.3~15초) 이탈: {inspection.duration_seconds:.2f}초"
        )
    if inspection.peak_dbfs > -1.0:
        raise ValueError(f"클리핑 여유 부족: peak {inspection.peak_dbfs:.1f} dBFS")
    if not -32.0 <= inspection.rms_dbfs <= -12.0:
        raise ValueError(f"RMS 권장 범위 이탈: {inspection.rms_dbfs:.1f} dBFS")
    return inspection


def _load_pcm16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class GuidePlayer:
    """승인된 WAV만 동기 재생하고 결과를 호출자에게 반환한다."""

    def __init__(self, backend: Any, assets_dir: Path | None = None):
        self.backend = backend
        self.assets_dir = assets_dir or (config.STT_ROOT / "assets")

    def play(
        self,
        code: GuideCode,
        *,
        exploration_resume_approved: bool = False,
    ) -> PlaybackResult:
        asset = GUIDE_ASSETS[code]
        if (
            asset.requires_exploration_resume
            and not exploration_resume_approved
        ):
            return PlaybackResult(
                code,
                PlaybackStatus.EXPLORATION_RESUME_NOT_APPROVED,
                "탐사 재개가 승인되지 않음",
            )

        path = self.assets_dir / asset.filename
        if not path.is_file():
            return PlaybackResult(code, PlaybackStatus.ASSET_NOT_FOUND, str(path))

        try:
            validate_wav(path)
            self.backend.play(_load_pcm16(path), config.FS)
            self.backend.wait()
        except Exception as exc:
            status = (
                PlaybackStatus.INVALID_ASSET
                if isinstance(exc, (ValueError, wave.Error))
                else PlaybackStatus.DEVICE_ERROR
            )
            return PlaybackResult(code, status, f"{type(exc).__name__}: {exc}")
        return PlaybackResult(code, PlaybackStatus.PLAYED, str(path))

    def play_text(
        self,
        text: str,
        *,
        exploration_resume_approved: bool = False,
    ) -> PlaybackResult:
        code = GUIDE_BY_TEXT.get(text)
        if code is None:
            return PlaybackResult(
                None, PlaybackStatus.UNAPPROVED_TEXT, "승인된 고정 문구가 아님"
            )
        return self.play(
            code,
            exploration_resume_approved=exploration_resume_approved,
        )

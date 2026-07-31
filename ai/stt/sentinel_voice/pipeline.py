"""
재난 구조 로봇 음성 파이프라인 (다턴 대화).

  트리거(VISION) → 세션 게이트 → 다턴 대화 세션 → 규칙 위험도 → 관제 보고 대기
                 → 안내 음성(승인된 사전녹음)

대화 세션은 명세 33-3 순서를 따른다.

  INTRO → COUNT → MOBILITY → URGENT → CLOSING

설계 원칙
  - 순서·실패 규칙은 `conversation.ConversationMachine`, 실물 입출력 연결은
    `session_runner.VoiceSessionRunner`가 담당한다. 이 모듈은 조립과 보고만 한다.
  - LLM은 진단하지 않고 사실만 구조화한다. 등급은 `safety.risk_assessment` 규칙값이다.
  - GMS 호출은 온라인 전용이다. 네트워크 단절이 확인되면 신규 세션을 시작하지 않고,
    STT 완료 후 GMS만 실패한 경우에 33-8 키워드 폴백을 쓴다.
  - 안내 음성은 승인된 사전녹음 WAV만 재생한다. 누락·오류 시 임의 TTS로 대체하지 않는다.
  - STT 실패를 무응답으로 기록하지 않는다(명세 33-3).

실행:
  python -m sentinel_voice.pipeline
"""
import sys
import time

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad, get_speech_timestamps

from . import config
from .config import FS
from .conversation import SessionResult, SessionState
from .guide_audio import GUIDE_ASSETS, GuidePlayer
from .llm import extract_with_status
from .report_delivery import queue_report
from .safety import coerce_report, risk_assessment
from .session_gate import SessionGateResult, check_session_gate
from .session_log import SessionLog
from .session_runner import SessionDependencies, VoiceSessionRunner

ASSETS = config.STT_ROOT / "assets"
guide_player = GuidePlayer(sd, ASSETS)


def say(message: str) -> None:
    """진행 로그. 콘솔 인코딩 때문에 세션이 죽으면 안 된다.

    cp949 콘솔은 이모지에서 `UnicodeEncodeError`를 던진다. 예외를 밖으로
    흘리면 로그 한 줄이 대화를 중단시키므로, 대체 문자로 낮춰서라도 출력한다.
    """
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            print(message.encode(encoding, "replace").decode(encoding, "replace"))
        except Exception:
            pass
    except Exception:
        pass


# 모델은 import 시점이 아니라 첫 사용 시 올린다. ROS 2 노드가 이 모듈을 import만
# 해도 Whisper가 메모리에 올라가면 노드 기동 순서와 RAM 피크를 통제할 수 없다.
_models: tuple = ()


def load_models() -> tuple:
    """(vad, stt)를 한 번만 로딩해 재사용한다."""
    global _models
    if not _models:
        say(f"모델 로딩... ({config.summary()})")
        _models = (
            load_silero_vad(),
            WhisperModel(
                config.STT_MODEL,
                device=config.DEVICE,
                compute_type=config.COMPUTE,
            ),
        )
    return _models


# ── 실물 입출력 구현 ──────────────────────────────────────────────
def record_microphone(seconds: float) -> np.ndarray:
    """BRIO 100 등 기본 입력 장치에서 동기 녹음한다."""
    frames = int(seconds * FS)
    wav = sd.rec(frames, samplerate=FS, channels=1, dtype="float32")
    sd.wait()
    return wav.reshape(-1)


def has_speech(wav: np.ndarray) -> bool:
    vad, _ = load_models()
    timestamps = get_speech_timestamps(
        torch.from_numpy(wav), vad, sampling_rate=FS
    )
    return len(timestamps) > 0


def transcribe(wav: np.ndarray) -> tuple[str, float]:
    _, stt = load_models()
    segments, _ = stt.transcribe(
        wav, initial_prompt=config.STT_PROMPT, **config.STT_DECODE
    )
    segments = list(segments)
    text = "".join(segment.text for segment in segments).strip()
    no_speech_prob = (
        float(np.mean([segment.no_speech_prob for segment in segments]))
        if segments
        else 1.0
    )
    return text, no_speech_prob


def build_dependencies() -> SessionDependencies:
    """ROS 2 노드 등 외부 호출자가 쓰는 실물 의존성 묶음. 여기서 모델을 올린다."""
    load_models()
    return SessionDependencies(
        record=record_microphone,
        has_speech=has_speech,
        transcribe=transcribe,
        extract=extract_with_status,
        player=guide_player,
    )


# ── 보고 ─────────────────────────────────────────────────────────
def speak(text: str, *, report_succeeded: bool = False):
    """승인된 안내 음성을 재생하고 실패를 명시적으로 기록한다."""
    say(f"🔊 로봇: {text}")
    result = guide_player.play_text(text, report_succeeded=report_succeeded)
    if not result.ok:
        say(f"   ⚠️ 안내 음성 재생 실패: {result.status.value} ({result.detail})")
    return result


def queue_and_announce(info: dict, *, session_log: SessionLog | None = None):
    """보고서를 전송 경계에 인계하고 발신 상태에 맞는 종료 안내를 재생한다.

    세션의 마지막 안내는 여기서만 나온다. 상태머신은 발신 상태를 알 수 없으므로
    종료 안내를 하지 않는다(`conversation.PROMPTS` 참고).
    """
    delivery = queue_report(info)
    say(f"📨 관제 보고 상태: {delivery.state.value} ({delivery.detail})")
    playback = speak(GUIDE_ASSETS[delivery.guide_code].text)
    if session_log is not None:
        session_log.announcement(
            delivery.guide_code.value, playback.status.value, delivery.detail
        )
    return delivery


def report_session(
    result: SessionResult,
    *,
    used_fallback: bool = False,
    session_log: SessionLog | None = None,
) -> dict:
    """세션 결과를 33-6 보고값으로 정리해 전송 대기에 넣는다."""
    info = coerce_report(dict(result.fields))
    risk = risk_assessment(info)
    # 턴 단위 판정만으로 false가 되면, 위험도 규칙이 확인을 요구하는 보고서가
    # "확인 불필요"로 관제에 올라간다. 둘 중 하나라도 true면 true다.
    info["operatorReviewRequired"] = bool(
        info.get("operatorReviewRequired") or risk["operatorReviewRequired"]
    )
    say(f"🩹 음성 세션 보고: {info}")
    say(f"🚨 위험도 참고값: {risk}")
    say(f"🧭 종료 상태: {result.state.value} / 사유: {result.termination_reason}")
    if used_fallback:
        say("ℹ️ 일부 응답은 33-8 키워드 폴백으로 구조화됨")
    if session_log is not None:
        session_log.report({"report": info, "risk": risk})
    queue_and_announce(info, session_log=session_log)
    return info


# ── 세션 실행 ────────────────────────────────────────────────────
def run(
    source: str = "VISION",
    *,
    gate_result: SessionGateResult | None = None,
    dependencies: SessionDependencies | None = None,
) -> SessionResult | None:
    """트리거 한 건에 대해 다턴 대화 세션을 1회 수행한다."""
    say(f"\n▶ 트리거: {source}")
    started = time.time()

    # 일반 인터넷이 아니라 GMS 호스트 도달성을 본다. 실패하면 세션을 시작하지 않는다.
    gate = gate_result or check_session_gate()
    if not gate.proceed:
        say(f"⚠️ 음성 세션 시작 차단: {gate.state.value}")
        speak(GUIDE_ASSETS[gate.guide_code].text)
        say(f"⏱ E2E: {time.time() - started:.1f}s")
        return None

    runner = VoiceSessionRunner(
        dependencies or build_dependencies(),
        on_event=say,
    )
    result = runner.run(source=source)

    if result.state == SessionState.FAILED_AUDIO:
        # 오디오 장치 오류는 요구조자 상태가 아니므로 관찰 실패로만 보고한다.
        say("⚠️ 오디오 장치 오류로 세션 종료 — 관제 확인 필요")

    report_session(
        result,
        used_fallback=runner.used_fallback,
        session_log=runner.session_log,
    )
    say(f"⏱ E2E: {time.time() - started:.1f}s")
    return result


if __name__ == "__main__":
    initial_gate = check_session_gate()
    run("VISION", gate_result=initial_gate)

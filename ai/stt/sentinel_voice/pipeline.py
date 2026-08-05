"""
재난 구조 로봇 음성 파이프라인 (다턴 대화).

  트리거(VISION) → 세션 게이트 → 다턴 대화 세션 → 규칙 위험도 → 관제 보고 대기
                 → 안내 음성(승인된 사전녹음)

대화 세션은 명세 33-3의 4분류를 따르되, 질문 순서는 부상 우선이다(S15P11A301-146 v2).

  INTRO → URGENT → MOBILITY → COUNT → CLOSING

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
from silero_vad import get_speech_timestamps, load_silero_vad

from . import config
from .config import FS
from .conversation import SessionResult, SessionState
from .guide_audio import GUIDE_ASSETS, GuidePlayer
from .llm import extract_with_status
from .remote_asr import RemoteASRClient
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


# VAD와 원격 ASR 클라이언트는 첫 사용 시 만들고 세션 간 재사용한다.
_models: tuple = ()


def load_models() -> tuple:
    """(vad, stt)를 한 번만 로딩해 재사용한다."""
    global _models
    if not _models:
        say(f"모델 로딩... ({config.summary()})")
        stt = RemoteASRClient(
            base_url=config.ASR_BASE_URL,
            api_key=config.ASR_API_KEY,
            timeout_seconds=config.ASR_TIMEOUT,
            connect_timeout_seconds=config.ASR_CONNECT_TIMEOUT,
            max_attempts=config.ASR_MAX_ATTEMPTS,
            retry_delay_seconds=config.ASR_RETRY_DELAY,
            allow_insecure_http=config.ASR_ALLOW_INSECURE_HTTP,
        )
        _models = (
            load_silero_vad(),
            stt,
        )
    return _models


# ── 실물 입출력 구현 ──────────────────────────────────────────────
def record_microphone(seconds: float) -> np.ndarray:
    """입력 장치에서 동기 녹음한다.

    장치를 지정하지 않으면 PortAudio 기본 장치를 쓴다. 젯슨에서 그 기본은
    `default`(ALSA)이고, 그것은 PulseAudio를 거쳐 **PulseAudio 기본 소스**로
    간다. 즉 이 함수의 입력은 녹화 파이프라인(`pulsesrc`)과 같은 기본값을
    공유한다 — 한쪽이 어긋나면 양쪽이 같이 어긋난다(S15P11A301-257).

    지정하려면 `SENTINEL_INPUT_DEVICE`를 쓴다. 젯슨에서는 PortAudio가 BRIO를
    이름으로 노출하지 않으므로 `PULSE_SOURCE` 또는 `pactl set-default-source`가
    실효 수단이다. 자세한 사정은 `config.INPUT_DEVICE` 주석에 있다.
    """
    frames = int(seconds * FS)
    wav = sd.rec(
        frames,
        samplerate=FS,
        channels=1,
        dtype="float32",
        device=config.INPUT_DEVICE,
    )
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
    return stt.transcribe(wav, sample_rate=FS, language="ko")


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
def speak(
    text: str,
    *,
    exploration_resume_approved: bool = False,
):
    """승인된 안내 음성을 재생하고 실패를 명시적으로 기록한다."""
    say(f"🔊 로봇: {text}")
    result = guide_player.play_text(
        text,
        exploration_resume_approved=exploration_resume_approved,
    )
    if not result.ok:
        say(f"   ⚠️ 안내 음성 재생 실패: {result.status.value} ({result.detail})")
    return result


def queue_and_announce(info: dict, *, session_log: SessionLog | None = None):
    """보고서를 전송 경계에 인계하고 종료 안내를 재생한다.

    세션의 마지막 안내는 여기서만 나온다. 상태머신은 발신 상태를 알 수 없으므로
    종료 안내를 하지 않는다(`conversation.PROMPTS` 참고).

    단독 실행(CLI)에는 임무 상태가 없어 탐사 재개를 가정하고 재생한다.
    실기 경로의 재개 판단은 ros_node가 임무 상태로 한다.
    """
    delivery = queue_report(info)
    say(f"📨 관제 보고 상태: {delivery.state.value} ({delivery.detail})")
    playback = speak(
        GUIDE_ASSETS[delivery.guide_code].text,
        exploration_resume_approved=True,
    )
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
        # 차단 안내 문구는 146 v2에서 삭제됐다. 차단은 로그로만 남긴다.
        say(f"⚠️ 음성 세션 시작 차단: {gate.state.value}")
        if gate.guide_code is not None:
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

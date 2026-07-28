"""
재난 구조 로봇 음성 파이프라인 (온디바이스, 네트워크 독립).

  트리거(VISION) → VAD 게이트 → STT → 환각 가드 → LLM 정보추출(GMS) → 규칙 triage
                 → 관제 서버 보고(시뮬) → 안내 음성(사전녹음 / TTS)

설계 원칙
  - SED는 시연 시나리오에서 제외됨. 트리거는 비전(객체탐지)이 기본.
    (SED 소스가 들어오면 "무응답=중증"으로 보고하는 경로는 유지)
  - LLM은 진단하지 않고 사실만 구조화. 등급은 safety.triage_rule 규칙으로 산출.
  - LLM은 GMS API(gpt-5-nano) 호출, 네트워크 불가 시 33-8 키워드 폴백(llm.extract).
  - TTS: 젯슨은 사전녹음 wav 재생(assets/), MeloTTS는 개발 PC 옵션(미설치면 자동 생략).
  - device/compute 는 config가 자동 감지(Jetson=cuda/int8, PC=cuda/float16).

실행:
  python -m sentinel_voice.pipeline  # 1=마이크 8초 녹음, 2=파일 입력
"""
import time

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad, get_speech_timestamps

from . import config
from .audio import load_mono
from .config import FS
from .llm import extract as extract_info
from .safety import coerce_report, is_valid_stt, report_defaults, risk_assessment

try:  # MeloTTS는 개발 PC 전용 옵션 — 젯슨(미설치)은 사전녹음 재생으로 동작
    from melo.api import TTS
    _HAS_MELO = True
except ImportError:
    _HAS_MELO = False

print(f"모델 로딩... ({config.summary()})")
vad = load_silero_vad()
stt = WhisperModel(config.STT_MODEL, device=config.DEVICE, compute_type=config.COMPUTE)
if _HAS_MELO:
    tts = TTS(language=config.TTS_LANG, device=config.DEVICE)
    tsid = tts.hps.data.spk2id

ASSETS = config.STT_ROOT / "assets"


def normalize(wav):
    rms = np.sqrt(np.mean(wav ** 2)) + 1e-9
    return np.clip(wav * (config.NORM_TARGET_RMS / rms), -1, 1).astype(np.float32)


def speak(text):
    """안내 음성. 우선순위: 사전녹음(assets) → MeloTTS(PC) → 텍스트만."""
    print(f"🔊 로봇: {text}")
    canned = config.GUIDE_WAVS.get(text)
    path = ASSETS / canned if canned else None
    if path and path.exists():
        sd.play(load_mono(path), FS)
        sd.wait()
    elif _HAS_MELO:
        tts.tts_to_file(text, tsid[config.TTS_LANG], "_tts.wav", speed=0.9)
        sd.play(load_mono("_tts.wav"), FS)
        sd.wait()
    else:
        print("   (사전녹음 없음·TTS 미탑재 — tools.make_tts_assets 참고)")


def has_speech(wav):
    ts = get_speech_timestamps(torch.from_numpy(wav), vad, sampling_rate=FS)
    return len(ts) > 0


def report(info, risk):
    print(f"🩹 음성 세션 보고: {info}")
    print(f"🚨 위험도 참고값: {risk}")
    print("📡 관제 서버 전송 (시뮬레이션)")


def unresponsive_report(reason):
    # 질문·청취가 정상 수행된 뒤 음성이 없을 때만 false로 기록한다.
    info = coerce_report(
        {
            "anyResponseDetected": False,
            "operatorReviewRequired": True,
            "terminationReason": "NORMAL",
        }
    )
    print(f"→ 무응답 관찰 사유: {reason}")
    report(info, risk_assessment(info))
    speak("구조대에 정보를 전달했습니다. 안심하세요.")


def run(source, wav):
    print(f"\n▶ 트리거: {source}")
    t0 = time.time()

    # 0) 원본 레벨로 무음 판정 (정규화가 노이즈 증폭하기 전에!)
    raw_rms = np.sqrt(np.mean(wav ** 2))
    silent = raw_rms < config.SILENCE_RMS
    if not silent:
        wav = normalize(wav)

    # 1) VAD 게이트
    if silent or not has_speech(wav):
        if source == "SED":
            print(f"→ 유효 음성 없음(raw_rms={raw_rms:.4f}) + SED → 무반응(중증)")
            unresponsive_report("신음 감지, 언어응답 없음")
        else:
            print("→ 유효 음성 없음. 재질문")
            speak("괜찮으시면 다시 한번 말씀해 주세요.")
        print(f"⏱ E2E: {time.time() - t0:.1f}s")
        return

    # 2) STT
    segs, _ = stt.transcribe(wav, initial_prompt=config.STT_PROMPT, **config.STT_DECODE)
    segs = list(segs)
    text = "".join(s.text for s in segs).strip()
    nsp = float(np.mean([s.no_speech_prob for s in segs])) if segs else 1.0
    print(f"📝 STT: '{text}' (ns={nsp:.2f})")

    # 3) 환각 가드 (프라이밍 echo 판정에는 STT initial_prompt 를 넘긴다)
    ok, why = is_valid_stt(text, nsp, config.STT_PROMPT)
    if not ok:
        print(f"→ STT 무효({why})")
        if source == "SED":
            unresponsive_report(f"STT무효:{why}")
        else:
            speak("잘 안 들려요. 다시 한번 말씀해 주세요.")
        print(f"⏱ E2E: {time.time() - t0:.1f}s")
        return

    # 4) LLM 추출(GMS) — 네트워크/API 실패 시 llm.extract 가 33-8 키워드 폴백으로 처리
    extraction, llm_source = extract_info(text)
    if llm_source != "GMS":
        print(f"→ 추출 경로: {llm_source} (오프라인 축소안)")

    # 5) 규칙 등급 + 보고 + 안내
    info = report_defaults()
    info.update(extraction)
    info["anyResponseDetected"] = True
    info["operatorReviewRequired"] = True
    info["terminationReason"] = "NORMAL"
    if info["reportedResponsiveCount"] is not None:
        info["reportedCountStatus"] = "SELF_REPORTED_GROUP_COUNT"
    info = coerce_report(info)
    report(info, risk_assessment(info))
    speak("구조대에 정보를 전달했습니다. 안심하세요.")
    print(f"⏱ E2E: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    mode = input("1=마이크, 2=파일: ").strip()
    src = (input("트리거(VISION/SED): ").strip().upper() or "VISION")
    if mode == "1":
        print("8초 녹음... 말하세요!")
        wav = sd.rec(int(8 * FS), samplerate=FS, channels=1, dtype="float32").reshape(-1)
        sd.wait()
    else:
        wav = load_mono(input("파일 경로: ").strip())
    run(src, wav)

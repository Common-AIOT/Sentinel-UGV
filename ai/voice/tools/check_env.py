"""
젯슨(및 개발 PC)에서 STT 파이프라인이 돌아갈 수 있는지 점검한다.

  python -m tools.check_env          # 원격 ASR 설정/오디오/GMS 점검
  python -m tools.check_env --load   # 원격 ASR 인증 전사/VAD 실제 로드

[OK]/[FAIL]/[WARN] 로 한 줄씩 출력. 하나라도 FAIL이면 그 원인부터 해결한다.
"""
import sys
import os
import platform

LOAD = "--load" in sys.argv
ok_all = True


def check(name, fn, warn=False):
    global ok_all
    try:
        msg = fn()
        print(f"[OK]   {name}" + (f" — {msg}" if msg else ""))
    except Exception as e:
        tag = "WARN" if warn else "FAIL"
        if not warn:
            ok_all = False
        print(f"[{tag}] {name}: {e}")


# ── 0. 플랫폼/젯슨 정보 ─────────────────────────────────────
def _platform():
    from sentinel_voice import config
    j = "Jetson" if config.is_jetson() else "non-Jetson"
    tegra = ""
    if os.path.exists("/etc/nv_tegra_release"):
        tegra = open("/etc/nv_tegra_release").read().splitlines()[0]
    return f"{platform.machine()} / {j} / {tegra}".strip(" /")


check("platform", _platform)


# ── 1. Torch (Jetson에서는 Silero VAD에만 사용) ─────────────
def _torch_runtime():
    import torch
    accelerator = (
        f"CUDA: {torch.cuda.get_device_name(0)}"
        if torch.cuda.is_available()
        else "CPU"
    )
    return f"torch {torch.__version__}, VAD runtime={accelerator}"


check("torch(VAD)", _torch_runtime)


# ── 2. 선택된 device/compute (config 자동감지 결과) ─────────
def _config():
    from sentinel_voice import config
    return config.summary()


check("config", _config)


# ── 3. 핵심 라이브러리 임포트 ───────────────────────────────
def _stt_client():
    from sentinel_voice import config

    __import__("httpx")
    if not config.ASR_API_KEY:
        raise RuntimeError(
            "SENTINEL_ASR_API_KEY 미설정 — ai/voice/.env에 추가 (커밋 금지)"
        )
    return f"remote FastAPI, {config.ASR_MODEL_LABEL}"


check("STT client", _stt_client)
check("silero-vad", lambda: __import__("silero_vad") and "")
check("sounddevice(장치)", lambda: str(len(__import__("sounddevice").query_devices())) + " devices")
check("librosa", lambda: __import__("librosa") and "")
def _guide_assets():
    from sentinel_voice import config
    from sentinel_voice.guide_audio import GUIDE_ASSETS

    assets = config.VOICE_ROOT / "assets"
    missing = [
        asset.filename
        for asset in GUIDE_ASSETS.values()
        if not (assets / asset.filename).is_file()
    ]
    if missing:
        raise RuntimeError(f"사전녹음 WAV {len(missing)}개 누락")
    return f"사전녹음 WAV {len(GUIDE_ASSETS)}개 보유"


check("guide-audio", _guide_assets)


# ── 4. GMS LLM (openai SDK + 키 설정 여부) ──────────────────
def _gms():
    from sentinel_voice import config
    __import__("openai")
    if not config.GMS_KEY:
        raise RuntimeError("GMS_KEY 미설정 — ai/voice/.env 에 GMS_KEY=... 추가 (커밋 금지)")
    return f"key 설정됨, model={config.LLM_MODEL}"


check("GMS(LLM)", _gms, warn=True)


# ── 5. 메모리/스왑 (8GB 젯슨 OOM 예방) ──────────────────────
def _mem():
    total = swap = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) / 1024 / 1024
                if line.startswith("SwapTotal:"):
                    swap = int(line.split()[1]) / 1024 / 1024
    except FileNotFoundError:
        raise RuntimeError("리눅스 아님(개발 PC) — 젯슨에서 재확인")
    if swap is not None and swap < 4:
        raise RuntimeError(f"스왑 {swap:.1f}GB < 4GB. 8GB 스왑 추가 권장(README 참고)")
    return f"RAM {total:.1f}GB, Swap {swap:.1f}GB"


check("memory/swap", _mem, warn=True)


# ── 6. (옵션) 실제 모델 로드 ───────────────────────────────
if LOAD:
    def _load_stt():
        from sentinel_voice import config
        import numpy as np

        from sentinel_voice.remote_asr import RemoteASRClient

        client = RemoteASRClient(
            base_url=config.ASR_BASE_URL,
            api_key=config.ASR_API_KEY,
            timeout_seconds=config.ASR_TIMEOUT,
            connect_timeout_seconds=config.ASR_CONNECT_TIMEOUT,
            max_attempts=config.ASR_MAX_ATTEMPTS,
            retry_delay_seconds=config.ASR_RETRY_DELAY,
            allow_insecure_http=config.ASR_ALLOW_INSECURE_HTTP,
        )
        try:
            health = client.health()
            # /health는 공개되어 있다. 짧은 디지털 무음을 한 번 전사해 Bearer 키와
            # /v1/asr 경로까지 검증하고 결과 텍스트는 버린다.
            client.transcribe(
                np.zeros(config.FS // 4, dtype=np.float32),
                sample_rate=config.FS,
            )
        finally:
            client.close()
        return (
            "원격 ASR health+인증 전사 성공 "
            f"(backend={health.get('backend')}, model={health.get('model')})"
        )

    def _load_vad():
        from silero_vad import load_silero_vad
        load_silero_vad()
        return "Silero VAD 로드 성공"

    def _gms_live():
        from sentinel_voice.llm import llm_extract
        info = llm_extract("테스트")
        return (
            "GMS 응답 정상 "
            f"(mobilityStatus={info.get('mobilityStatus')}, "
            f"urgentConditionReported={info.get('urgentConditionReported')})"
        )

    check("STT 로드", _load_stt)
    check("VAD 로드", _load_vad)
    check("GMS 실호출", _gms_live, warn=True)   # 네트워크·키 필요, 실패해도 33-8 폴백 존재
else:
    print("[i]    모델 실로드는 생략(--load 로 최종 확인). ")

print("\n" + ("✅ 전부 통과 — STT 구동 준비 완료" if ok_all
             else "⚠️ FAIL 항목부터 해결하세요 (WARN은 시연 필수는 아님)"))
sys.exit(0 if ok_all else 1)

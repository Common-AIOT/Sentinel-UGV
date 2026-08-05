"""
젯슨(및 개발 PC)에서 STT 파이프라인이 돌아갈 수 있는지 점검한다.

  python -m tools.check_env          # 임포트/CUDA/장치/모델가용성 점검
  python -m tools.check_env --load   # 실제 STT/VAD 모델까지 로드(무거움, 최종 확인)

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


# ── 1. Torch + CUDA (GPU 가속의 전제) ───────────────────────
def _torch_cuda():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available()=False (Jetson용 torch 휠 확인)")
    return f"torch {torch.__version__}, GPU: {torch.cuda.get_device_name(0)}"


check("torch+CUDA", _torch_cuda)


# ── 2. 선택된 device/compute (config 자동감지 결과) ─────────
def _config():
    from sentinel_voice import config
    return config.summary()


check("config", _config)


# ── 3. 핵심 라이브러리 임포트 ───────────────────────────────
def _stt_client():
    from sentinel_voice import config

    if config.STT_BACKEND == "remote":
        __import__("httpx")
        if not config.ASR_API_KEY:
            raise RuntimeError(
                "SENTINEL_ASR_API_KEY 미설정 — ai/stt/.env에 추가 (커밋 금지)"
            )
        return f"remote, {config.ASR_MODEL_LABEL}"
    __import__("faster_whisper")
    return f"local, {config.STT_MODEL}/{config.COMPUTE}"


check("STT client", _stt_client)
check("silero-vad", lambda: __import__("silero_vad") and "")
check("sounddevice(장치)", lambda: str(len(__import__("sounddevice").query_devices())) + " devices")
check("librosa", lambda: __import__("librosa") and "")
def _guide_assets():
    from sentinel_voice import config
    from sentinel_voice.guide_audio import GUIDE_ASSETS

    assets = config.STT_ROOT / "assets"
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
        raise RuntimeError("GMS_KEY 미설정 — ai/stt/.env 에 GMS_KEY=... 추가 (커밋 금지)")
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
        if config.STT_BACKEND == "remote":
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
            client.close()
            return f"원격 ASR 설정 검증 성공 ({config.ASR_MODEL_LABEL})"

        from faster_whisper import WhisperModel

        WhisperModel(
            config.STT_MODEL,
            device=config.DEVICE,
            compute_type=config.COMPUTE,
        )
        return f"{config.STT_MODEL}/{config.COMPUTE} 로드 성공"

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

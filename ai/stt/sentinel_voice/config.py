# config.py
"""
STT-LLM-TTS 음성 파이프라인 공통 설정.

개발 PC(x86 + RTX)와 Jetson Orin Nano(ARM64 + Ampere)를 같은 코드로 돌리기 위해
device / compute_type 를 자동 감지한다. 환경 변수로 언제든 덮어쓸 수 있다.

  SENTINEL_DEVICE   = cuda | cpu            (기본: cuda 가능하면 cuda)
  SENTINEL_COMPUTE  = int8 | float16 | ...  (기본: Jetson=int8, 그 외 GPU=float16)
  SENTINEL_STT_MODEL= tiny|base|small|...   (기본: small)
  SENTINEL_LLM      = GMS 모델명             (기본: gpt-5-nano — 팀 결정 2026-07-24)
  GMS_KEY           = GMS API 키 (ai/stt/.env 파일 지원, 커밋 금지)
"""
import os
from pathlib import Path

STT_ROOT = Path(__file__).resolve().parent.parent

FS = 16000  # 파이프라인 전체 표준 샘플레이트(Hz)


def _load_dotenv():
    """ai/stt/.env 의 KEY=VALUE 를 환경변수로 로드(이미 설정된 값은 유지)."""
    path = STT_ROOT / ".env"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _has_cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def is_jetson() -> bool:
    """NVIDIA Tegra(Jetson) 보드인지 감지."""
    if os.path.exists("/etc/nv_tegra_release"):
        return True
    try:
        with open("/proc/device-tree/model", "r", errors="ignore") as f:
            return "NVIDIA" in f.read() or "Orin" in f.read()
    except Exception:
        return False


def pick_device() -> str:
    env = os.getenv("SENTINEL_DEVICE")
    if env:
        return env
    return "cuda" if _has_cuda() else "cpu"


def pick_compute(device: str) -> str:
    env = os.getenv("SENTINEL_COMPUTE")
    if env:
        return env
    if device != "cuda":
        return "int8"          # CPU 폴백
    # Jetson 8GB는 메모리가 빠듯하므로 int8 권장. 데스크톱 GPU는 float16.
    return "int8" if is_jetson() else "float16"


DEVICE = pick_device()
COMPUTE = pick_compute(DEVICE)

# ── 모델 선택 ────────────────────────────────────────────────
STT_MODEL = os.getenv("SENTINEL_STT_MODEL", "small")   # faster-whisper

# LLM — 팀 결정(2026-07-24): GMS API(gpt-5-nano). 온디바이스 ollama에서 전환.
# 근거: 젯슨 실측 피크 5.62GB·page cache OOM (docs/메모리-예산.md)
LLM_MODEL = os.getenv("SENTINEL_LLM", "gpt-5-nano")
GMS_BASE_URL = os.getenv("SENTINEL_GMS_BASE",
                         "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
GMS_KEY = os.getenv("GMS_KEY", "")
LLM_TIMEOUT = float(os.getenv("SENTINEL_LLM_TIMEOUT", "10"))  # 초과 시 33-8 폴백

TTS_LANG = "KR"   # MeloTTS(개발 PC 전용). 젯슨은 사전녹음 재생 — GUIDE_WAVS 참고

# 고정 안내 문구 → 사전녹음 파일 (assets/). tools.make_tts_assets로 생성.
GUIDE_WAVS = {
    "구조대에 정보를 전달했습니다. 안심하세요.": "guide_reported.wav",
    "괜찮으시면 다시 한번 말씀해 주세요.": "guide_retry.wav",
    "잘 안 들려요. 다시 한번 말씀해 주세요.": "guide_unclear.wav",
}

# ── 튜닝된 파라미터(측정으로 고정) ────────────────────────────
# Silero VAD
VAD_OPTS = dict(
    threshold=0.5,
    min_speech_duration_ms=150,
    min_silence_duration_ms=500,
    speech_pad_ms=300,
)

# faster-whisper 디코딩 옵션 (저SNR·약한발화 강건성 + 환각 억제)
STT_DECODE = dict(
    language="ko",
    vad_filter=True,
    vad_parameters=VAD_OPTS,
    beam_size=5,
    temperature=(0.0, 0.2),
    condition_on_previous_text=False,
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
)
# 재난 상황 자주 나오는 단어로 STT 바이어스(도메인 프라이밍)
STT_PROMPT = "살려주세요, 도와주세요, 다쳤어요, 가스, 화재"

# 원본(정규화 전) RMS가 이보다 작으면 사실상 무음으로 판정
SILENCE_RMS = 0.005

# 정규화 목표 RMS
NORM_TARGET_RMS = 0.08

PROMPT_PATH = STT_ROOT / "prompts" / "triage_extract.txt"


def summary() -> str:
    return (
        f"device={DEVICE} compute={COMPUTE} jetson={is_jetson()} "
        f"stt={STT_MODEL} llm={LLM_MODEL}"
    )

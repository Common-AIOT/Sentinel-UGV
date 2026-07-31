# config.py
"""
STT-LLM-TTS 음성 파이프라인 공통 설정.

개발 PC(x86 + RTX)와 Jetson Orin Nano(ARM64 + Ampere)를 같은 코드로 돌리기 위해
device / compute_type 를 자동 감지한다. 환경 변수로 언제든 덮어쓸 수 있다.

  SENTINEL_DEVICE   = cuda | cpu            (기본: cuda 가능하면 cuda)
  SENTINEL_COMPUTE  = int8 | float16 | ...  (기본: Jetson=int8, 그 외 GPU=float16)
  SENTINEL_STT_MODEL= tiny|base|small|...   (기본: small)
  SENTINEL_LLM      = GMS 모델명             (기본: gpt-5.4-mini — Jira 118 실측 선정)
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

# LLM — 온디바이스 ollama에서 GMS API로 전환 후 Jira 118 비교로 모델 선정.
# 근거: 젯슨 로컬 3b 피크 5.62GB·OOM, gpt-5.4-mini 프롬프트 v2 44/44 정답.
LLM_MODEL = os.getenv("SENTINEL_LLM", "gpt-5.4-mini")
GMS_BASE_URL = os.getenv("SENTINEL_GMS_BASE",
                         "https://gms.ssafy.io/gmsapi/api.openai.com/v1")
GMS_KEY = os.getenv("GMS_KEY", "")
LLM_TIMEOUT = float(os.getenv("SENTINEL_LLM_TIMEOUT", "10"))  # 초과 시 33-8 폴백
GMS_MAX_ATTEMPTS = int(os.getenv("SENTINEL_GMS_MAX_ATTEMPTS", "2"))
GMS_RETRY_DELAY = float(os.getenv("SENTINEL_GMS_RETRY_DELAY", "0.5"))
GMS_PROBE_TIMEOUT = float(os.getenv("SENTINEL_GMS_PROBE_TIMEOUT", "2"))

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

# 안내 재생이 끝난 뒤 청취를 시작하기까지 대기(초).
# sounddevice의 wait()는 로컬 PortAudio 스트림 기준이라, Bluetooth A2DP의
# 싱크 버퍼(100~250ms)만큼 스피커에서 안내 꼬리가 더 재생된다. 그 구간을 녹음하면
# 로봇 자기 음성이 요구조자 응답으로 오인된다(S15P11A301-165).
LISTEN_DELAY = float(os.getenv("SENTINEL_LISTEN_DELAY", "0.3"))

# 청취 결과가 안내 문구 자체로 판정되는 기준. 문자 바이그램 포함률과 최소 길이다.
#
# 0.9는 실측 분포에서 고른 값이다. 안내 문구와 그 꼬리 조각은 1.00으로 모이고,
# 실제 응답은 최대 0.78("스스로 움직일 수 있어요" — 질문의 단어를 그대로 쓴 답)이라
# 두 무리 사이가 비어 있다. 0.8로 두면 그 답변과 여유가 0.02뿐이어서, 걸을 수 있는
# 부상자를 무응답으로 보고한다. 측정표는 docs/README.md 11-3.
#
# 실기기 재검증에서 조정할 수 있도록 환경변수로 열어 둔다. 조각난 에코가 0.9에
# 못 미쳐 새는 경우가 관측되면 178이 저장한 청취 원본으로 값을 다시 고른다.
ECHO_MATCH_RATIO = float(os.getenv("SENTINEL_ECHO_MATCH_RATIO", "0.9"))
ECHO_MIN_CHARS = int(os.getenv("SENTINEL_ECHO_MIN_CHARS", "8"))

PROMPT_PATH = STT_ROOT / "prompts" / "triage_extract.txt"


def summary() -> str:
    return (
        f"device={DEVICE} compute={COMPUTE} jetson={is_jetson()} "
        f"stt={STT_MODEL} llm={LLM_MODEL}"
    )

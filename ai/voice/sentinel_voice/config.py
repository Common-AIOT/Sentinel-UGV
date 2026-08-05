# config.py
"""
STT-LLM-사전녹음 음성 파이프라인 공통 설정.

개발 PC와 Jetson이 같은 FastAPI 원격 ASR·GMS 설정을 사용한다. Jetson 여부는
진단 요약에만 기록하며 STT 모델이나 연산 장치는 Jetson에서 선택하지 않는다.

  SENTINEL_ASR_BASE_URL=https://...          (remote 전용)
  SENTINEL_ASR_API_KEY=...                   (remote 전용, 커밋 금지)
  SENTINEL_LLM      = GMS 모델명             (기본: gpt-5.4-mini — Jira 118 실측 선정)
  GMS_KEY           = GMS API 키 (ai/voice/.env 파일 지원, 커밋 금지)
"""
import os
from pathlib import Path

VOICE_ROOT = Path(__file__).resolve().parent.parent

FS = 16000  # 파이프라인 전체 표준 샘플레이트(Hz)


def _load_dotenv():
    """ai/voice/.env 의 KEY=VALUE 를 환경변수로 로드(이미 설정된 값은 유지)."""
    path = VOICE_ROOT / ".env"
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def is_jetson() -> bool:
    """NVIDIA Tegra(Jetson) 보드인지 감지."""
    if os.path.exists("/etc/nv_tegra_release"):
        return True
    try:
        with open("/proc/device-tree/model", "r", errors="ignore") as f:
            return "NVIDIA" in f.read() or "Orin" in f.read()
    except Exception:
        return False


# STT 운영 경로는 Qwen3-ASR FastAPI 원격 서버 하나다.
# API 키는 서버 인증에만 쓰며 로그·세션 파일에 남기지 않는다.
ASR_BASE_URL = os.getenv("SENTINEL_ASR_BASE_URL", "http://127.0.0.1:18100")
ASR_API_KEY = os.getenv("SENTINEL_ASR_API_KEY", "")
ASR_TIMEOUT = float(os.getenv("SENTINEL_ASR_TIMEOUT", "8"))
ASR_CONNECT_TIMEOUT = float(os.getenv("SENTINEL_ASR_CONNECT_TIMEOUT", "2"))
ASR_MAX_ATTEMPTS = int(os.getenv("SENTINEL_ASR_MAX_ATTEMPTS", "2"))
ASR_RETRY_DELAY = float(os.getenv("SENTINEL_ASR_RETRY_DELAY", "0.2"))
ASR_ALLOW_INSECURE_HTTP = os.getenv(
    "SENTINEL_ASR_ALLOW_INSECURE_HTTP", "0"
).strip().lower() in {"1", "true", "yes", "on"}
ASR_MODEL_LABEL = os.getenv("SENTINEL_ASR_MODEL_LABEL", "Qwen/Qwen3-ASR-1.7B")

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

# 원본(정규화 전) RMS가 이보다 작으면 사실상 무음으로 판정
SILENCE_RMS = 0.005

# 디지털 무음 판정 — "조용한 방"이 아니라 "캡처 경로가 죽었다"는 서명이다.
#
# 살아 있는 마이크는 조용한 방에서도 정확히 0을 내지 않는다. 열잡음·양자화 오차가
# 항상 있다. 우리 실측에서 조용하다고 판정된 구간도 rms 0.0038이었다(0이 아니다).
# 반대로 다음 경우에는 전 구간이 정확히 0이 된다.
#
#   - 입력 소스가 아무것도 연결되지 않은 단자로 지정돼 있다
#   - 소스가 출력 모니터(스피커로 나가는 소리를 되받는 가상 장치)로 잡혀 있다
#   - 소스가 음소거되어 있거나 볼륨이 0이다
#
# 2026-08-04 젯슨에서 첫 경우가 실제로 났다(S15P11A301-257). PulseAudio 기본
# 소스가 BRIO가 아니라 보드의 빈 아날로그 입력이었고, 리허설 이벤트 영상 295초가
# 전부 peak 0이었다. 음성 세션도 같은 기본값을 쓰므로 같은 무음을 받는다.
#
# **이 상황을 무음으로 판정하면 안 된다.** 무음 → anyResponseDetected=false →
# riskLevel=IMMEDIATE가 되어, 마이크 사망이 "의식 없는 요구조자"로 보고된다.
# README 10-3 치명 오류 목록의 "시스템 장애를 요구조자 무응답으로 변환"이다.
#
# 부동소수 변환 잔차를 감안해 정확히 0이 아니라 아주 작은 값으로 둔다. 이 값과
# SILENCE_RMS(0.005) 사이는 세 자리 이상 벌어져 있어 진짜 무응답을 삼키지 않는다.
SILENT_INPUT_PEAK = float(os.getenv("SENTINEL_SILENT_INPUT_PEAK", "1e-6"))

# 녹음 입력 장치. None이면 PortAudio 기본 장치를 쓴다(현행 동작).
#
# 젯슨에서는 이름으로 BRIO를 지목할 수 없다 — PulseAudio가 USB 카드를 독점해
# ALSA 직접 접근이 막히고, PortAudio 목록에 hw:0이 아예 나타나지 않는다. 그쪽에서
# 실효가 있는 수단은 PulseAudio 소스를 지정하는 것이다(PULSE_SOURCE 환경변수 또는
# pactl set-default-source). 이 값은 장치 이름을 노출하는 환경(윈도우 등)과
# 인덱스로 지목하는 경우를 위한 것이다.
#
# 숫자면 인덱스로, 그 밖이면 이름 문자열로 sounddevice에 넘긴다.
INPUT_DEVICE: str | int | None = os.getenv("SENTINEL_INPUT_DEVICE") or None
if isinstance(INPUT_DEVICE, str) and INPUT_DEVICE.lstrip("-").isdigit():
    INPUT_DEVICE = int(INPUT_DEVICE)

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

PROMPT_PATH = VOICE_ROOT / "prompts" / "triage_extract.txt"


def summary() -> str:
    return (
        f"jetson={is_jetson()} stt_backend=remote "
        f"stt={ASR_MODEL_LABEL} "
        f"llm={LLM_MODEL}"
    )

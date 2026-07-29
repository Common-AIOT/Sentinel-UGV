"""GMS와 오프라인 키워드 파서를 동일한 33-6 보고 계약으로 제공한다."""

from __future__ import annotations

import json
import re

from . import config
from .gms_resilience import GmsCallResult, call_with_limited_retry
from .safety import coerce_extraction


PROMPT = config.PROMPT_PATH.read_text(encoding="utf-8")
_client = None

_KOREAN_NUMBERS = {
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
}
_URGENT_WORDS = ("심한 출혈", "피가 많이", "숨을 못", "호흡 곤란", "불", "화재", "가스")


def _gms():
    global _client
    if _client is None:
        from openai import OpenAI

        if not config.GMS_KEY:
            raise RuntimeError("GMS_KEY 미설정: ai/stt/.env에 GMS_KEY를 설정하세요")
        _client = OpenAI(
            base_url=config.GMS_BASE_URL,
            api_key=config.GMS_KEY,
            timeout=config.LLM_TIMEOUT,
        )
    return _client


def request_options(model: str) -> dict:
    """GMS 모델 계열에 맞는 최소 추론 옵션을 반환한다."""
    normalized = model.lower()
    if normalized.startswith("gpt-5.4"):
        return {"reasoning_effort": "none"}
    if normalized.startswith("gpt-5"):
        return {"reasoning_effort": "minimal"}
    return {}


def llm_extract(text, model=None):
    """GMS 응답을 허용 필드만 남긴 33-6 보고값으로 반환한다."""
    selected_model = model or config.LLM_MODEL
    response = _gms().chat.completions.create(
        model=selected_model,
        messages=[
            {"role": "user", "content": PROMPT.replace("{input_text}", text)}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=300,
        **request_options(selected_model),
    )
    return coerce_extraction(json.loads(response.choices[0].message.content))


def _reported_count(text: str) -> int | None:
    if re.search(
        r"(저\s*(혼자|한\s*명\s*뿐|밖에\s*없)|"
        r"저\s*말고(?:는)?[^.?!]{0,20}아무도\s*없)",
        text,
    ):
        return 1
    match = re.search(r"(한|하나|두|둘|세|셋|네|넷|다섯|\d+)\s*(명|사람)", text)
    if not match:
        return None
    token = match.group(1)
    count = int(token) if token.isdigit() else _KOREAN_NUMBERS[token]
    return count


def keyword_extract(text):
    """GMS 없이 명시적으로 발화된 예·아니오·숫자·긴급어만 추출한다."""
    normalized = (text or "").strip()
    count = _reported_count(normalized)
    cannot_move = bool(
        re.search(r"(못\s*(가|움직)|움직일\s*수\s*없|이동\s*불가)", normalized)
    )
    can_move = bool(
        re.search(r"(움직일\s*수\s*있|이동\s*가능|갈\s*수\s*있)", normalized)
    )
    urgent = any(word in normalized for word in _URGENT_WORDS)
    urgent_denied = bool(
        re.search(r"(출혈|호흡\s*곤란|위험).{0,5}(없|아니)", normalized)
    )

    return coerce_extraction(
        {
            "reportedResponsiveCount": count,
            "mobilityStatus": (
                "NO" if cannot_move else "YES" if can_move else "UNKNOWN"
            ),
            "urgentConditionReported": (
                "YES" if urgent else "NO" if urgent_denied else "UNKNOWN"
            ),
        }
    )


def extract_with_status(text) -> GmsCallResult:
    """GMS 호출 결과와 재시도 횟수·분류된 실패 원인을 함께 반환한다."""

    extraction, attempts, failure = call_with_limited_retry(
        lambda: llm_extract(text),
        max_attempts=config.GMS_MAX_ATTEMPTS,
        retry_delay_seconds=config.GMS_RETRY_DELAY,
    )
    if extraction is not None:
        return GmsCallResult(extraction, "GMS", attempts)

    print(
        f"[WARN] GMS 추출 실패(kind={failure.kind.value}, "
        f"type={failure.error_type}, attempts={attempts}) -> 33-8 키워드 폴백"
    )
    return GmsCallResult(keyword_extract(text), "FALLBACK", attempts, failure)


def extract(text):
    """기존 호출자를 위한 `(추출값, 출처)` 호환 진입점."""

    result = extract_with_status(text)
    return result.extraction, result.source

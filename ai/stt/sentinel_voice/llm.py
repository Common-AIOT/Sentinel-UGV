"""GMS와 오프라인 키워드 파서를 동일한 33-6 보고 계약으로 제공한다."""

from __future__ import annotations

import json
import re

from . import config
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


def llm_extract(text, model=None):
    """GMS 응답을 허용 필드만 남긴 33-6 보고값으로 반환한다."""
    response = _gms().chat.completions.create(
        model=model or config.LLM_MODEL,
        messages=[
            {"role": "user", "content": PROMPT.replace("{input_text}", text)}
        ],
        response_format={"type": "json_object"},
        reasoning_effort="minimal",
        max_completion_tokens=300,
    )
    return coerce_extraction(json.loads(response.choices[0].message.content))


def _reported_count(text: str) -> int | None:
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


def extract(text):
    """GMS 우선, 실패 시 동일 계약의 33-8 키워드 파서로 폴백한다."""
    try:
        return llm_extract(text), "GMS"
    except Exception as error:
        print(
            f"[WARN] GMS 추출 실패({type(error).__name__}: {error}) "
            "-> 33-8 키워드 폴백"
        )
        return keyword_extract(text), "FALLBACK"

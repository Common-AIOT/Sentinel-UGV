# llm.py
"""
LLM 정보 구조화 — GMS API(gpt-5-nano) 호출 + 오프라인 규칙 폴백.

팀 결정(2026-07-24): LLM은 젯슨 온디바이스(ollama)가 아니라 GMS API 호출로 전환.
근거: 젯슨 실측에서 qwen2.5:3b 피크 5.62GB·page cache OOM (docs/메모리-예산.md).
네트워크 불가 시에는 명세 33-8 축소안(키워드 파서)으로 폴백해 핵심 보고를 유지한다.

  extract(text) -> (info, source)   # source: "GMS" | "FALLBACK"
"""
import json
import re

import config
from safety import strip_hallucinated, coerce_defaults

PROMPT = open(config.PROMPT_PATH, encoding="utf-8").read()

_client = None


def _gms():
    global _client
    if _client is None:
        from openai import OpenAI
        if not config.GMS_KEY:
            raise RuntimeError("GMS_KEY 미설정 — ai/stt/.env 에 GMS_KEY=... 추가")
        _client = OpenAI(base_url=config.GMS_BASE_URL, api_key=config.GMS_KEY,
                         timeout=config.LLM_TIMEOUT)
    return _client


def llm_extract(text, model=None):
    """GMS(gpt-5-nano)로 발화를 triage 스키마 JSON으로 구조화한다."""
    r = _gms().chat.completions.create(
        model=model or config.LLM_MODEL,
        messages=[{"role": "user", "content": PROMPT.replace("{input_text}", text)}],
        response_format={"type": "json_object"},
        reasoning_effort="minimal",      # 단순 추출 태스크 — 추론 토큰 낭비 방지
        max_completion_tokens=300,
    )
    info = json.loads(r.choices[0].message.content)
    return coerce_defaults(strip_hallucinated(info, text))


# ── 명세 33-8 축소안: 네트워크 불가 시 키워드 파서 ─────────────────────
PAIN_WORDS = ["다리", "팔", "머리", "가슴", "배", "허리", "목", "어깨", "무릎", "발", "손"]
HAZARD_WORDS = ["가스", "화재", "연기", "붕괴", "불"]
_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5}


def keyword_extract(text):
    """LLM 없이 발화에서 사실만 추출. 불확실하면 미확인으로 두는 보수적 파서."""
    toks = text.split()
    speech = "완전문장" if len(toks) >= 4 else ("단어만" if toks else "불가")
    cannot_move = bool(re.search(r"(움직|일어).{0,6}(못|없)|못 ?(움직|일어)", text))
    info = {
        "consciousness": "명료" if speech == "완전문장" else "미확인",
        "speech": speech,
        "pain_location": [w for w in PAIN_WORDS if w in text],
        "hazard": [w for w in HAZARD_WORDS if w in text],
        "can_move": "불가" if cannot_move else "미확인",
        "additional_victims": 0,
        "raw_note": "[33-8 키워드파서] " + text[:80],
    }
    m = re.search(r"(한|두|세|네|다섯|\d+) ?(명|사람)", text)
    if m:
        info["additional_victims"] = _NUM.get(m.group(1)) or (
            int(m.group(1)) if m.group(1).isdigit() else 1)
    return coerce_defaults(info)


def extract(text):
    """GMS 우선, 실패 시 33-8 키워드 폴백. 호출부는 이 함수만 사용한다."""
    try:
        return llm_extract(text), "GMS"
    except Exception as e:
        print(f"→ GMS 호출 실패({type(e).__name__}: {e}) — 33-8 키워드 폴백")
        return keyword_extract(text), "FALLBACK"

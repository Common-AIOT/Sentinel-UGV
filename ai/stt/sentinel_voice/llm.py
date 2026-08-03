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
# 긴급 근거가 되는 언급. 부상·통증도 포함한다 — 정도를 재지 않는 것이
# 프롬프트(`prompts/triage_extract.txt`)와 같은 태도다. "다리를 다쳤어요"가
# UNKNOWN으로 올라가면 구조대원에게 쓸모없는 보고가 된다.
_URGENT_PATTERN = re.compile(
    # '피가 계속 나요'처럼 사이에 말이 끼는 경우가 있어 '피가'만으로 잡는다.
    # 부정("피가 안 나요")은 _DENIAL_PATTERN이 걸러낸다.
    r"(다쳤|다친|부상|골절|부러|아파|아프|"
    r"출혈|피\s*(가|를)|"
    r"숨\s*(을|이)?\s*(못|안)|숨쉬기|호흡\s*곤란|"
    r"눌려|눌렸|끼여|끼였|깔려|깔렸|"  # 끼임·압착. GMS 실호출 대조로 추가(2026-08-04)
    r"불이|화재|가스|연기)"
)

# 부정 표현. 위 언급 뒤에 이것이 붙으면 긴급 근거로 세지 않는다.
_DENIAL_PATTERN = re.compile(r"^.{0,8}?(없|아니|괜찮|안\s*나)")

# 통증·끼임 때문에 일어나거나 움직이기 어렵다는 표현. 부상 언급만으로는 여기 걸리지
# 않는다 — 이동에 대한 말이 있어야 한다("다리를 다쳤어요"는 이동 UNKNOWN).
_HARD_TO_MOVE_PATTERN = re.compile(
    r"((일어나|움직이|걷|나가).{0,10}(아파|아프|힘들|어렵|안\s*되)"
    r"|(아파서|아프고|눌려서|끼여서|깔려서).{0,10}(일어나|움직|못|안))"
)


def _mentioned_without_denial(pattern: "re.Pattern[str]", text: str) -> bool:
    """부정이 붙지 않은 언급이 하나라도 있으면 True.

    "다친 곳은 없습니다"는 '다친' 뒤에 '없'이 와서 False다.
    "출혈은 없는데 숨쉬기가 힘들어요"는 '출혈'은 부정되지만 '숨쉬기'가 남아 True다.
    """
    for match in pattern.finditer(text):
        if not _DENIAL_PATTERN.match(text[match.end() :]):
            return True
    return False


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

    # 언급된 사람이 대답을 못 한다고 하면 응답 인원에서 뺀다. 화자만 남는다.
    if re.search(r"(대답|말|응답).{0,6}(안\s*(해|하|함)|못\s*(해|하|함)|없)", text):
        return 1

    # "두 명 더", "옆에 한 명" 처럼 주변 인원을 덧붙인 표현은 화자를 더한다.
    # "저 포함해서 세 명", "여기 두 명"처럼 총인원을 말한 경우는 그대로 둔다.
    tail = text[match.end() :]
    if re.search(r"^\s*(더|또)", tail) or re.search(
        r"(옆|근처|주변|같이|함께)\s*(에|에는)?\s*$", text[: match.start()]
    ):
        count += 1
    return count


def keyword_extract(text):
    """GMS 없이 발화된 예·아니오·숫자·부상·긴급어를 추출한다.

    프롬프트(`prompts/triage_extract.txt`)와 **같은 태도**를 지킨다 — 말한 것을
    그대로 받아들이고 정도를 재지 않는다. 두 경로가 어긋나면 GMS 장애 시 보고
    내용이 달라져 관제가 혼란해진다.

    다만 규칙 기반이라 LLM보다 거칠다. 어순이 뒤바뀐 표현이나 완곡한 표현은
    놓친다. 33-8이 요구하는 축소 동작이며 이것이 주 경로를 대체하지는 않는다.
    """
    normalized = (text or "").strip()
    count = _reported_count(normalized)
    cannot_move = bool(
        re.search(r"(못\s*(가|움직|일어나)|움직일\s*수\s*없|일어날\s*수\s*없|이동\s*불가)", normalized)
        or _HARD_TO_MOVE_PATTERN.search(normalized)
    )
    can_move = bool(
        re.search(r"(움직일\s*수\s*있|이동\s*가능|갈\s*수\s*있|걸을\s*수\s*있)", normalized)
    )
    # 부정된 언급은 긴급 근거로 세지 않는다. "다친 곳은 없습니다"가 '다친'에
    # 걸려 YES가 되면 정반대 보고가 나간다.
    #
    # 부정을 먼저 걸러내고 남은 언급만 본다. 그래서 "출혈은 없는데 숨쉬기가
    # 힘들어요"는 출혈이 빠져도 호흡 언급이 남아 YES가 된다 — 프롬프트의
    # "하나를 부정하더라도 다른 증상이 명시되면 YES"와 같은 결과다.
    urgent = _mentioned_without_denial(_URGENT_PATTERN, normalized)
    urgent_denied = bool(_URGENT_PATTERN.search(normalized)) and not urgent

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

"""GMS 후보 모델의 구조화 정보 추출 품질과 지연을 비교한다.

합성 문장과 사전 확정 정답만 사용한다. GMS 키와 인증 헤더는 결과에 기록하지 않는다.

실행 예:
    python -m bench.gms_model_bench --dry-run
    python -m bench.gms_model_bench --runs 1 --confirm-live
    python -m bench.gms_model_bench --models gpt-5-nano,gpt-5.4-nano
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from sentinel_voice import config
from sentinel_voice.safety import coerce_extraction


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "bench" / "fixtures" / "gms-extraction-cases.json"
DEFAULT_MODELS = (
    "gpt-5-nano",
    "gemini-3.5-flash",
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
)
ANTHROPIC_BASE_URL = (
    "https://gms.ssafy.io/gmsapi/api.anthropic.com/v1/messages"
)
GEMINI_BASE_URL = (
    "https://gms.ssafy.io/gmsapi/"
    "generativelanguage.googleapis.com/v1beta/models"
)
SLOTS = (
    "reportedResponsiveCount",
    "mobilityStatus",
    "urgentConditionReported",
)


class SchemaValidationError(ValueError):
    def __init__(
        self,
        raw_content: str,
        parsed: Any,
        normalized_response: "NormalizedResponse | None" = None,
    ):
        super().__init__("GMS 응답이 엄격한 3필드 추출 스키마를 위반했습니다.")
        self.raw_content = raw_content
        self.parsed = parsed
        self.normalized_response = normalized_response


@dataclass(frozen=True)
class Case:
    case_id: str
    text: str
    expected: dict[str, Any]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedResponse:
    raw_content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


def load_cases(path: Path) -> list[Case]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        Case(
            case_id=row["caseId"],
            text=row["text"],
            expected=coerce_extraction(row["expected"]),
            tags=tuple(row.get("tags", [])),
        )
        for row in payload["cases"]
    ]
    if not cases:
        raise ValueError("벤치마크 케이스가 비어 있습니다.")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("caseId가 중복되었습니다.")
    return cases


def prompt_version() -> str:
    content = config.PROMPT_PATH.read_bytes()
    return f"sha256:{hashlib.sha256(content).hexdigest()[:12]}"


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * percent / 100
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def score_extraction(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> dict[str, Any]:
    expected = coerce_extraction(expected)
    actual = coerce_extraction(actual)
    correct = {slot: actual[slot] == expected[slot] for slot in SLOTS}
    hallucinated = [
        slot
        for slot in SLOTS
        if expected[slot] in (None, "UNKNOWN")
        and actual[slot] not in (None, "UNKNOWN")
    ]
    critical_error = (
        (expected["mobilityStatus"] == "NO" and actual["mobilityStatus"] == "YES")
        or (
            expected["urgentConditionReported"] == "YES"
            and actual["urgentConditionReported"] == "NO"
        )
    )
    return {
        "slotCorrect": correct,
        "correctSlots": sum(correct.values()),
        "allSlotsCorrect": all(correct.values()),
        "hallucinatedSlots": hallucinated,
        "criticalError": critical_error,
    }


def _usage(response: Any) -> dict[str, int | None]:
    if isinstance(response, NormalizedResponse):
        return {
            "promptTokens": response.prompt_tokens,
            "completionTokens": response.completion_tokens,
            "totalTokens": response.total_tokens,
        }
    usage = getattr(response, "usage", None)
    return {
        "promptTokens": getattr(usage, "prompt_tokens", None),
        "completionTokens": getattr(usage, "completion_tokens", None),
        "totalTokens": getattr(usage, "total_tokens", None),
    }


def _status_code(error: Exception) -> int | None:
    direct = getattr(error, "status_code", None)
    if direct is not None:
        return direct
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None)


def _error_usage(error: Exception) -> dict[str, int | None]:
    response = getattr(error, "normalized_response", None)
    return _usage(response)


def is_strict_extraction(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != set(SLOTS):
        return False
    count = value["reportedResponsiveCount"]
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count < 1
    ):
        return False
    if value["mobilityStatus"] not in {"YES", "NO", "UNKNOWN"}:
        return False
    if value["urgentConditionReported"] not in {"YES", "NO", "UNKNOWN"}:
        return False
    return True


def _raw_content(response: Any) -> str | None:
    if isinstance(response, NormalizedResponse):
        return response.raw_content
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return None


def request_options(model: str) -> dict[str, Any]:
    """모델 공급자가 지원하는 옵션만 보낸다.

    GPT-5 계열은 최소 reasoning을 지정하지 않으면 짧은 JSON 작업에서도 출력 토큰을
    추론에 소진해 빈 content를 돌려줄 수 있다. Gemini·Claude에는 OpenAI 전용 옵션을
    보내지 않는다.
    """
    normalized = model.lower()
    if normalized.startswith("gpt-5.4"):
        return {"reasoning_effort": "none"}
    if normalized.startswith("gpt-5"):
        return {"reasoning_effort": "minimal"}
    return {}


def provider_for_model(model: str) -> str:
    normalized = model.lower()
    if normalized.startswith("claude-"):
        return "anthropic"
    if normalized.startswith("gemini-"):
        return "gemini"
    return "openai"


_JSON_CODE_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


def unwrap_json_code_fence(raw_content: str) -> str:
    """응답 전체를 감싼 Markdown JSON 코드블록만 제거한다.

    앞뒤 설명문이나 코드블록 내부의 추가 코드블록은 허용하지 않는다. 공급자별
    예외를 두지 않고 모든 모델 응답에 동일하게 적용한다.
    """
    stripped = raw_content.strip()
    match = _JSON_CODE_FENCE.fullmatch(stripped)
    if not match:
        return stripped
    body = match.group("body").strip()
    return body if "```" not in body else stripped


def _validate_and_coerce(raw_content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(unwrap_json_code_fence(raw_content))
    except json.JSONDecodeError as error:
        raise SchemaValidationError(raw_content, None) from error
    if not is_strict_extraction(parsed):
        raise SchemaValidationError(raw_content, parsed)
    return coerce_extraction(parsed)


def call_openai_model(
    client: Any, model: str, text: str
) -> tuple[dict[str, Any], Any]:
    from sentinel_voice.llm import PROMPT

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": PROMPT.replace("{input_text}", text)}
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=300,
        **request_options(model),
    )
    raw_content = response.choices[0].message.content
    return _validate_and_coerce(raw_content), response


def call_anthropic_model(
    http_client: Any, model: str, text: str
) -> tuple[dict[str, Any], NormalizedResponse]:
    from sentinel_voice.llm import PROMPT

    response = http_client.post(
        ANTHROPIC_BASE_URL,
        headers={
            "Content-Type": "application/json",
            "x-api-key": config.GMS_KEY,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": PROMPT.replace("{input_text}", text),
                }
            ],
        },
    )
    response.raise_for_status()
    payload = response.json()
    raw_content = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    )
    usage = payload.get("usage", {})
    prompt_tokens = usage.get("input_tokens")
    completion_tokens = usage.get("output_tokens")
    normalized = NormalizedResponse(
        raw_content=raw_content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=(
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        ),
    )
    try:
        extraction = _validate_and_coerce(raw_content)
    except SchemaValidationError as error:
        error.normalized_response = normalized
        raise
    return extraction, normalized


def call_gemini_model(
    http_client: Any, model: str, text: str
) -> tuple[dict[str, Any], NormalizedResponse]:
    from sentinel_voice.llm import PROMPT

    response = http_client.post(
        f"{GEMINI_BASE_URL}/{model}:generateContent",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": config.GMS_KEY,
        },
        json={
            "contents": [
                {
                    "parts": [
                        {"text": PROMPT.replace("{input_text}", text)}
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 300,
                # 이 작업은 3필드 JSON 추출이다. 기본 Thinking을 켜면 Gemini 3.5가
                # 출력 한도를 추론 토큰으로 소진해 불완전 JSON을 반환할 수 있다.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
    )
    response.raise_for_status()
    payload = response.json()
    parts = payload["candidates"][0]["content"]["parts"]
    raw_content = "".join(part.get("text", "") for part in parts)
    usage = payload.get("usageMetadata", {})
    normalized = NormalizedResponse(
        raw_content=raw_content,
        prompt_tokens=usage.get("promptTokenCount"),
        completion_tokens=usage.get("candidatesTokenCount"),
        total_tokens=usage.get("totalTokenCount"),
    )
    try:
        extraction = _validate_and_coerce(raw_content)
    except SchemaValidationError as error:
        error.normalized_response = normalized
        raise
    return extraction, normalized


def call_model(
    openai_client: Any,
    http_client: Any,
    model: str,
    text: str,
) -> tuple[dict[str, Any], Any]:
    provider = provider_for_model(model)
    if provider == "anthropic":
        return call_anthropic_model(http_client, model, text)
    if provider == "gemini":
        return call_gemini_model(http_client, model, text)
    return call_openai_model(openai_client, model, text)


def benchmark(
    *,
    cases: list[Case],
    models: list[str],
    runs: int,
    invoke: Callable[[str, str], tuple[dict[str, Any], Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in models:
        for case in cases:
            for run_index in range(1, runs + 1):
                started_at = datetime.now(timezone.utc).isoformat()
                started = time.perf_counter()
                try:
                    actual, response = invoke(model, case.text)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    score = score_extraction(case.expected, actual)
                    row = {
                        "model": model,
                        "caseId": case.case_id,
                        "runIndex": run_index,
                        "startedAt": started_at,
                        "tags": list(case.tags),
                        "inputText": case.text,
                        "expected": case.expected,
                        "actual": actual,
                        "rawContent": _raw_content(response),
                        "success": True,
                        "schemaValid": True,
                        "httpStatus": 200,
                        "elapsedMs": round(elapsed_ms, 3),
                        "errorType": None,
                        "errorMessage": None,
                        **_usage(response),
                        **score,
                    }
                except Exception as error:  # 실제 API 오류도 결과의 일부다.
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    row = {
                        "model": model,
                        "caseId": case.case_id,
                        "runIndex": run_index,
                        "startedAt": started_at,
                        "tags": list(case.tags),
                        "inputText": case.text,
                        "expected": case.expected,
                        "actual": getattr(error, "parsed", None),
                        "rawContent": getattr(error, "raw_content", None),
                        "success": False,
                        "schemaValid": False,
                        "httpStatus": _status_code(error),
                        "elapsedMs": round(elapsed_ms, 3),
                        "errorType": type(error).__name__,
                        "errorMessage": str(error)[:300],
                        **_error_usage(error),
                        "slotCorrect": {slot: False for slot in SLOTS},
                        "correctSlots": 0,
                        "allSlotsCorrect": False,
                        "hallucinatedSlots": [],
                        "criticalError": False,
                    }
                rows.append(row)
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    models = list(dict.fromkeys(row["model"] for row in rows))
    for model in models:
        selected = [row for row in rows if row["model"] == model]
        successful = [row for row in selected if row["success"]]
        latencies = [row["elapsedMs"] for row in successful]
        total_slots = len(selected) * len(SLOTS)
        correct_slots = sum(row["correctSlots"] for row in selected)
        fully_correct = sum(row["allSlotsCorrect"] for row in selected)
        hallucinations = sum(len(row["hallucinatedSlots"]) for row in selected)
        token_values = [
            row["totalTokens"]
            for row in selected
            if row["totalTokens"] is not None
        ]
        consistency_values = []
        case_ids = list(dict.fromkeys(row["caseId"] for row in selected))
        for case_id in case_ids:
            case_rows = [
                row
                for row in selected
                if row["caseId"] == case_id and row["success"]
            ]
            if not case_rows:
                continue
            signatures = [
                json.dumps(row["actual"], sort_keys=True, ensure_ascii=False)
                for row in case_rows
            ]
            most_common = max(signatures.count(value) for value in set(signatures))
            consistency_values.append(most_common / len(case_rows) * 100)
        summaries.append(
            {
                "model": model,
                "requests": len(selected),
                "successRatePct": round(len(successful) / len(selected) * 100, 2),
                "schemaValidRatePct": round(
                    sum(row["schemaValid"] for row in selected)
                    / len(selected)
                    * 100,
                    2,
                ),
                "slotAccuracyPct": round(correct_slots / total_slots * 100, 2),
                "exactMatchPct": round(fully_correct / len(selected) * 100, 2),
                "outputConsistencyPct": _round_optional(
                    statistics.mean(consistency_values)
                    if consistency_values
                    else None
                ),
                "hallucinatedSlotCount": hallucinations,
                "criticalErrorCount": sum(row["criticalError"] for row in selected),
                "latencyP50Ms": _round_optional(percentile(latencies, 50)),
                "latencyP95Ms": _round_optional(percentile(latencies, 95)),
                "latencyMaxMs": _round_optional(max(latencies) if latencies else None),
                "averageTotalTokens": _round_optional(
                    statistics.mean(token_values) if token_values else None
                ),
                "errorCount": len(selected) - len(successful),
            }
        )
    return summaries


def _round_optional(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def write_results(
    output_dir: Path,
    *,
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "gms-bench-raw.json").write_text(
        json.dumps({"metadata": metadata, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "gms-bench-summary.json").write_text(
        json.dumps(
            {"metadata": metadata, "models": summaries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if summaries:
        with (output_dir / "gms-bench-summary.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
            writer.writeheader()
            writer.writerows(summaries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--case-id",
        action="append",
        help="지정한 caseId만 실행한다. 여러 번 지정할 수 있다.",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results" / "gms-bench")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API를 호출하지 않고 데이터셋과 설정만 검증한다.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="예정된 GMS 호출 수를 확인했고 실제 크레딧 사용에 동의한다.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    models = [model.strip() for model in args.models.split(",") if model.strip()]
    if args.runs < 1:
        raise SystemExit("--runs는 1 이상이어야 합니다.")
    if not models:
        raise SystemExit("비교할 모델이 없습니다.")
    cases = load_cases(args.dataset)
    if args.case_id:
        requested = set(args.case_id)
        cases = [case for case in cases if case.case_id in requested]
        missing = requested - {case.case_id for case in cases}
        if missing:
            raise SystemExit(f"존재하지 않는 caseId: {', '.join(sorted(missing))}")
    print(
        f"[OK] dataset={len(cases)} cases, models={len(models)}, "
        f"runs={args.runs}, requests={len(cases) * len(models) * args.runs}, "
        f"prompt={prompt_version()}"
    )
    if args.dry_run:
        print("[OK] dry-run 완료 - GMS API를 호출하지 않았습니다.")
        return 0
    if not config.GMS_KEY:
        raise SystemExit("GMS_KEY가 없습니다. ai/stt/.env에 설정하세요.")
    if not args.confirm_live:
        raise SystemExit(
            "실제 GMS 호출에는 --confirm-live가 필요합니다. "
            "위 requests 수를 확인하세요."
        )

    from openai import OpenAI
    import httpx

    openai_client = OpenAI(
        base_url=config.GMS_BASE_URL,
        api_key=config.GMS_KEY,
        timeout=config.LLM_TIMEOUT,
    )
    http_client = httpx.Client(timeout=config.LLM_TIMEOUT)
    run_id = str(uuid.uuid4())
    rows = benchmark(
        cases=cases,
        models=models,
        runs=args.runs,
        invoke=lambda model, text: call_model(
            openai_client, http_client, model, text
        ),
    )
    summaries = summarize(rows)
    metadata = {
        "runId": run_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset),
        "caseCount": len(cases),
        "models": models,
        "runsPerCase": args.runs,
        "promptVersion": prompt_version(),
        "gmsBaseUrl": config.GMS_BASE_URL,
    }
    write_results(args.output_dir, rows=rows, summaries=summaries, metadata=metadata)
    for summary in summaries:
        print(
            f"{summary['model']}: success={summary['successRatePct']}% "
            f"slots={summary['slotAccuracyPct']}% "
            f"exact={summary['exactMatchPct']}% "
            f"p95={summary['latencyP95Ms']}ms "
            f"critical={summary['criticalErrorCount']}"
        )
    print(f"[OK] 결과 저장: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

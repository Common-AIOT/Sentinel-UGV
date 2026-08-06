"""ASR 소음 전사를 운영 GMS 프롬프트로 해석해 최종 슬롯 복원율을 측정한다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from sentinel_voice import config
from sentinel_voice.conversation import PROMPTS, QuestionCode
from sentinel_voice.llm import keyword_extract, llm_extract


DEFAULT_CONDITIONS = (
    "clean",
    "siren@0db",
    "moto@5db",
    "moto@0db",
    "realmotor@5db",
    "realmotor@0db",
)
DEFAULT_CASES = (
    "domain-04",  # URGENT NO
    "domain-06",  # URGENT YES
    "domain-10",  # MOBILITY NO
    "domain-13",  # COUNT 3
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_manifest(path: Path) -> dict[str, dict]:
    rows = (
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return {row["caseId"]: row for row in rows}


def _is_risk_to_safe(field: str, expected: object, actual: object) -> bool:
    return bool(
        (field == "urgentConditionReported" and expected == "YES" and actual == "NO")
        or (field == "mobilityStatus" and expected == "NO" and actual == "YES")
    )


def select_observations(
    raw: dict, *, conditions: tuple[str, ...], source_cases: tuple[str, ...]
) -> list[dict]:
    selected = []
    by_key = {
        (row["case_id"].split("--", 1)[0], row["condition"]): row
        for row in raw["observations"]
        if row.get("ok") and row.get("run") == 1
    }
    for condition in conditions:
        for source_case in source_cases:
            try:
                selected.append(by_key[(source_case, condition)])
            except KeyError as error:
                raise ValueError(f"missing ASR observation: {source_case} {condition}") from error
    return selected


def benchmark(
    *,
    manifest_path: Path,
    asr_raw_path: Path,
    conditions: tuple[str, ...] = DEFAULT_CONDITIONS,
    source_cases: tuple[str, ...] = DEFAULT_CASES,
) -> dict:
    manifest = _load_manifest(manifest_path)
    observations = select_observations(
        _load_json(asr_raw_path), conditions=conditions, source_cases=source_cases
    )
    results = []
    for index, asr in enumerate(observations, start=1):
        source_case = asr["case_id"].split("--", 1)[0]
        source = next(
            row for case_id, row in manifest.items() if case_id.startswith(f"{source_case}--")
        )
        question = QuestionCode(source["question"])
        field = source["expectedField"]
        expected = source["expectedValue"]
        started = time.perf_counter()
        error = None
        extraction = None
        try:
            extraction = llm_extract(asr["text"], PROMPTS[question])
        except Exception as exception:  # 호출 실패도 평가 결과다.
            error = f"{type(exception).__name__}: {exception}"
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        fallback = keyword_extract(asr["text"])
        actual = extraction.get(field) if extraction else None
        fallback_actual = fallback.get(field)
        llm_correct = actual == expected
        fallback_correct = fallback_actual == expected
        critical_failed = (
            asr.get("critical_groups_total", 0) > asr.get("critical_groups_preserved", 0)
        )
        results.append(
            {
                "caseId": asr["case_id"],
                "condition": asr["condition"],
                "question": question.value,
                "referenceText": source["transcript"],
                "asrText": asr["text"],
                "field": field,
                "expected": expected,
                "fallbackValue": fallback_actual,
                "llmValue": actual,
                "fallbackCorrect": fallback_correct,
                "llmCorrect": llm_correct,
                "criticalAsrFailure": critical_failed,
                "llmRecovered": critical_failed and llm_correct,
                "llmImprovedOverFallback": not fallback_correct and llm_correct,
                "llmRegressedFromFallback": fallback_correct and not llm_correct,
                "riskToSafe": _is_risk_to_safe(field, expected, actual),
                "unsupportedWrongAssertion": bool(
                    critical_failed and not llm_correct and actual not in (None, "UNKNOWN")
                ),
                "latencyMs": latency_ms,
                "error": error,
            }
        )
        print(
            f"[{index:02d}/{len(observations)}] {asr['condition']} {source_case} "
            f"expected={expected} llm={actual} correct={llm_correct}",
            flush=True,
        )

    succeeded = [row for row in results if row["error"] is None]
    latencies = sorted(row["latencyMs"] for row in succeeded)

    def percentile(fraction: float) -> float | None:
        if not latencies:
            return None
        return latencies[round((len(latencies) - 1) * fraction)]

    summary = {
        "model": config.LLM_MODEL,
        "cases": len(results),
        "requestsSucceeded": len(succeeded),
        "slotCorrect": sum(row["llmCorrect"] for row in succeeded),
        "slotAccuracy": (
            sum(row["llmCorrect"] for row in succeeded) / len(succeeded)
            if succeeded
            else None
        ),
        "fallbackSlotCorrect": sum(row["fallbackCorrect"] for row in results),
        "fallbackSlotAccuracy": (
            sum(row["fallbackCorrect"] for row in results) / len(results)
            if results
            else None
        ),
        "criticalAsrFailures": sum(row["criticalAsrFailure"] for row in results),
        "llmRecovered": sum(row["llmRecovered"] for row in succeeded),
        "llmImprovedOverFallback": sum(
            row["llmImprovedOverFallback"] for row in succeeded
        ),
        "llmRegressedFromFallback": sum(
            row["llmRegressedFromFallback"] for row in succeeded
        ),
        "riskToSafe": sum(row["riskToSafe"] for row in succeeded),
        "unsupportedWrongAssertions": sum(
            row["unsupportedWrongAssertion"] for row in succeeded
        ),
        "latencyP50Ms": percentile(0.5),
        "latencyP95Ms": percentile(0.95),
    }
    return {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "manifest": str(manifest_path.resolve()),
        "asrRaw": str(asr_raw_path.resolve()),
        "selection": {"conditions": list(conditions), "sourceCases": list(source_cases)},
        "summary": summary,
        "observations": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--asr-raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--condition", action="append", dest="conditions")
    parser.add_argument("--source-case", action="append", dest="source_cases")
    args = parser.parse_args()
    result = benchmark(
        manifest_path=args.manifest,
        asr_raw_path=args.asr_raw,
        conditions=tuple(args.conditions or DEFAULT_CONDITIONS),
        source_cases=tuple(args.source_cases or DEFAULT_CASES),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["requestsSucceeded"] == result["summary"]["cases"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

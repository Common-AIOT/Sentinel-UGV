"""Benchmark the authenticated local extraction LLM against fixed safety cases."""

from __future__ import annotations

import argparse
import csv
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from bench.gms_model_bench import (
    NormalizedResponse,
    benchmark,
    load_cases,
    prompt_version,
    summarize,
)
from gpu_llm_server.app import EXTRACTION_SCHEMA
from sentinel_voice.llm import PROMPT

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASETS = (
    ROOT / "bench" / "fixtures" / "gms-prompt-hard-cases.json",
    ROOT / "bench" / "fixtures" / "gms-external-independent-v2.json",
)


def load_unique_cases(paths: list[Path]):
    cases = []
    seen: set[str] = set()
    for path in paths:
        for case in load_cases(path):
            if case.case_id in seen:
                raise ValueError(f"duplicate caseId across datasets: {case.case_id}")
            seen.add(case.case_id)
            cases.append(case)
    return cases


def call_local_model(
    client: httpx.Client,
    *,
    base_url: str,
    api_key: str,
    model: str,
    text: str,
) -> tuple[dict[str, Any], NormalizedResponse]:
    response = client.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "user", "content": PROMPT.replace("{input_text}", text)}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage_extraction",
                    "strict": True,
                    "schema": EXTRACTION_SCHEMA,
                },
            },
            "max_completion_tokens": 160,
        },
    )
    response.raise_for_status()
    payload = response.json()
    raw = payload["choices"][0]["message"]["content"]
    actual = json.loads(raw)
    usage = payload.get("usage", {})
    return actual, NormalizedResponse(
        raw_content=raw,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def write_results(
    output_dir: Path,
    *,
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "local-llm-shadow-raw.json").write_text(
        json.dumps({"metadata": metadata, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "local-llm-shadow-summary.json").write_text(
        json.dumps(
            {"metadata": metadata, "models": summaries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if summaries:
        with (output_dir / "local-llm-shadow-summary.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
            writer.writeheader()
            writer.writerows(summaries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, action="append")
    parser.add_argument("--model", default="Qwen/Qwen3.5-4B")
    parser.add_argument("--base-url", default="http://127.0.0.1:18200")
    parser.add_argument("--api-key-env", default="LOCAL_LLM_API_KEY")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--vram-mib", type=int)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "results" / "local-llm-shadow"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    datasets = args.dataset or list(DEFAULT_DATASETS)
    cases = load_unique_cases(datasets)
    requests = len(cases) * args.runs
    print(
        f"[OK] cases={len(cases)} runs={args.runs} requests={requests} "
        f"prompt={prompt_version()}"
    )
    if args.dry_run:
        return 0
    if not args.confirm_live:
        raise SystemExit("live execution requires --confirm-live")
    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")

    with httpx.Client(timeout=args.timeout) as client:
        rows = benchmark(
            cases=cases,
            models=[args.model],
            runs=args.runs,
            invoke=lambda model, text: call_local_model(
                client,
                base_url=args.base_url,
                api_key=api_key,
                model=model,
                text=text,
            ),
        )
    summaries = summarize(rows)
    metadata = {
        "runId": str(uuid.uuid4()),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "datasets": [str(path) for path in datasets],
        "caseCount": len(cases),
        "runsPerCase": args.runs,
        "promptVersion": prompt_version(),
        "baseUrl": args.base_url,
        "vramMiB": args.vram_mib,
    }
    write_results(args.output_dir, rows=rows, summaries=summaries, metadata=metadata)
    print(json.dumps(summaries[0], ensure_ascii=False))
    return 2 if summaries[0]["criticalErrorCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

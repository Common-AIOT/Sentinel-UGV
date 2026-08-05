"""Compare ASR HTTP endpoints with the same labelled audio corpus.

The command intentionally talks only to the versioned ``POST /v1/asr``
contract.  Models can therefore be started one at a time on a shared GPU and
their outputs appended to the same reproducible report.

Example::

    ASR_API_KEY=... python -m bench.asr_shadow_bench \
      --manifest bench/fixtures/asr-shadow/manifest.jsonl \
      --endpoint qwen=http://127.0.0.1:18100 \
      --endpoint whisper-large-v3=http://127.0.0.1:18101 \
      --runs 3 --output-dir results/asr-shadow
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "bench" / "fixtures" / "asr-shadow" / "manifest.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "results" / "asr-shadow"


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    audio: Path
    reference: str
    language: str | None
    condition: str
    critical_term_groups: tuple[tuple[str, ...], ...]
    expected_polarity: str
    risk_patterns: tuple[str, ...]
    safe_patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Endpoint:
    label: str
    base_url: str


@dataclass(frozen=True, slots=True)
class Observation:
    endpoint: str
    case_id: str
    run: int
    ok: bool
    text: str
    language: str | None
    condition: str
    latency_ms: float
    inference_ms: float | None
    duration_seconds: float | None
    cer: float | None
    wer: float | None
    cer_edits: int | None
    cer_reference_units: int | None
    wer_edits: int | None
    wer_reference_units: int | None
    critical_groups_preserved: int
    critical_groups_total: int
    predicted_polarity: str
    risk_to_safe: bool
    hallucinated_non_speech: bool
    error_code: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    """Apply the same conservative transform to reference and hypothesis."""

    text = unicodedata.normalize("NFKC", str(text)).lower()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _distance(left: list[str], right: list[str]) -> int:
    if not left:
        return len(right)
    previous = list(range(len(right) + 1))
    for row, left_item in enumerate(left, start=1):
        current = [row]
        for column, right_item in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def error_counts(
    reference: str, hypothesis: str, *, characters: bool
) -> tuple[int, int]:
    reference = normalize(reference)
    hypothesis = normalize(hypothesis)
    if characters:
        left = list(reference.replace(" ", ""))
        right = list(hypothesis.replace(" ", ""))
    else:
        left = reference.split()
        right = hypothesis.split()
    return _distance(left, right), len(left)


def error_rate(reference: str, hypothesis: str, *, characters: bool) -> float:
    edits, units = error_counts(reference, hypothesis, characters=characters)
    if not units:
        return 0.0 if not edits else 1.0
    return edits / units


def percentile(values: Iterable[float], percent: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def parse_endpoint(value: str) -> Endpoint:
    label, separator, base_url = value.partition("=")
    if not separator or not label.strip() or not base_url.strip():
        raise argparse.ArgumentTypeError("endpoint must be LABEL=URL")
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
        raise argparse.ArgumentTypeError(
            "endpoint must use HTTPS or a loopback HTTP tunnel"
        )
    return Endpoint(label.strip(), base_url)


def _strings(value: Any, field: str, case_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{case_id}.{field} must be a string array")
    return tuple(value)


def load_cases(path: Path) -> list[Case]:
    path = Path(path).resolve()
    cases: list[Case] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = str(row.get("caseId", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"line {line_number}: caseId is missing or duplicated")
        seen.add(case_id)
        relative = Path(str(row.get("audio", "")))
        audio = relative if relative.is_absolute() else path.parent / relative
        if not audio.is_file():
            raise ValueError(f"{case_id}.audio does not exist: {audio}")
        groups_value = row.get("criticalTermGroups", [])
        if not isinstance(groups_value, list):
            raise TypeError(f"{case_id}.criticalTermGroups must be an array")
        groups: list[tuple[str, ...]] = []
        for group in groups_value:
            if not isinstance(group, list) or not group or not all(
                isinstance(term, str) and term.strip() for term in group
            ):
                raise ValueError(
                    f"{case_id}.criticalTermGroups entries must be non-empty string arrays"
                )
            groups.append(tuple(group))
        polarity = str(row.get("expectedPolarity", "unknown")).lower()
        if polarity not in {"risk", "safe", "unknown"}:
            raise ValueError(f"{case_id}.expectedPolarity is invalid")
        cases.append(
            Case(
                case_id=case_id,
                audio=audio.resolve(),
                reference=str(row.get("transcript", "")),
                language=str(row["language"]) if row.get("language") else None,
                condition=str(row.get("condition", "clean")),
                critical_term_groups=tuple(groups),
                expected_polarity=polarity,
                risk_patterns=_strings(row.get("riskPatterns"), "riskPatterns", case_id),
                safe_patterns=_strings(row.get("safePatterns"), "safePatterns", case_id),
            )
        )
    if not cases:
        raise ValueError("manifest contains no cases")
    return cases


def _contains(text: str, pattern: str) -> bool:
    return normalize(pattern).replace(" ", "") in normalize(text).replace(" ", "")


def predict_polarity(case: Case, hypothesis: str) -> str:
    risk = any(_contains(hypothesis, pattern) for pattern in case.risk_patterns)
    safe = any(_contains(hypothesis, pattern) for pattern in case.safe_patterns)
    if risk:
        return "risk"
    if safe:
        return "safe"
    return "unknown"


def score(case: Case, hypothesis: str) -> dict[str, Any]:
    preserved = sum(
        any(_contains(hypothesis, alternative) for alternative in group)
        for group in case.critical_term_groups
    )
    predicted = predict_polarity(case, hypothesis)
    non_speech = case.condition in {"silence", "noise-only"}
    cer_edits, cer_units = error_counts(case.reference, hypothesis, characters=True)
    wer_edits, wer_units = error_counts(case.reference, hypothesis, characters=False)
    return {
        "cer": None if not case.reference else error_rate(case.reference, hypothesis, characters=True),
        "wer": None if not case.reference else error_rate(case.reference, hypothesis, characters=False),
        "cer_edits": cer_edits if case.reference else None,
        "cer_reference_units": cer_units if case.reference else None,
        "wer_edits": wer_edits if case.reference else None,
        "wer_reference_units": wer_units if case.reference else None,
        "critical_groups_preserved": preserved,
        "critical_groups_total": len(case.critical_term_groups),
        "predicted_polarity": predicted,
        "risk_to_safe": case.expected_polarity == "risk" and predicted == "safe",
        "hallucinated_non_speech": non_speech and bool(normalize(hypothesis)),
    }


def _multipart(audio: bytes, filename: str, language: str | None) -> tuple[bytes, str]:
    boundary = f"----a301-asr-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    if language:
        field("language", language)
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="audio"; filename="{filename}"\r\n'
                "Content-Type: audio/wav\r\n\r\n"
            ).encode(),
            audio,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def invoke_http(
    endpoint: Endpoint,
    case: Case,
    api_key: str,
    timeout_seconds: float,
    request_id: str,
) -> dict[str, Any]:
    body, boundary = _multipart(case.audio.read_bytes(), case.audio.name, case.language)
    request = urllib.request.Request(
        f"{endpoint.base_url}/v1/asr",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Request-ID": request_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            code = payload.get("error", {}).get("code")
        except (UnicodeDecodeError, json.JSONDecodeError):
            code = None
        raise RuntimeError(code or f"HTTP_{error.code}") from error


def benchmark(
    cases: list[Case],
    endpoints: list[Endpoint],
    *,
    api_key: str,
    runs: int,
    timeout_seconds: float,
    invoke: Callable[[Endpoint, Case, str, float, str], dict[str, Any]] = invoke_http,
) -> list[Observation]:
    observations: list[Observation] = []
    for endpoint in endpoints:
        for case in cases:
            for run in range(1, runs + 1):
                started = time.perf_counter()
                try:
                    payload = invoke(
                        endpoint,
                        case,
                        api_key,
                        timeout_seconds,
                        f"bench-{endpoint.label}-{case.case_id}-{run}",
                    )
                    elapsed = (time.perf_counter() - started) * 1000
                    hypothesis = str(payload.get("text", ""))
                    scored = score(case, hypothesis)
                    observations.append(
                        Observation(
                            endpoint=endpoint.label,
                            case_id=case.case_id,
                            run=run,
                            ok=True,
                            text=hypothesis,
                            language=payload.get("language"),
                            condition=case.condition,
                            latency_ms=round(elapsed, 3),
                            inference_ms=_optional_float(payload.get("inference_ms")),
                            duration_seconds=_optional_float(payload.get("duration_seconds")),
                            error_code=None,
                            **scored,
                        )
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    elapsed = (time.perf_counter() - started) * 1000
                    observations.append(
                        Observation(
                            endpoint=endpoint.label,
                            case_id=case.case_id,
                            run=run,
                            ok=False,
                            text="",
                            language=None,
                            condition=case.condition,
                            latency_ms=round(elapsed, 3),
                            inference_ms=None,
                            duration_seconds=None,
                            cer=None,
                            wer=None,
                            cer_edits=None,
                            cer_reference_units=None,
                            wer_edits=None,
                            wer_reference_units=None,
                            critical_groups_preserved=0,
                            critical_groups_total=len(case.critical_term_groups),
                            predicted_polarity="unknown",
                            risk_to_safe=False,
                            hallucinated_non_speech=False,
                            error_code=str(error)[:80],
                        )
                    )
    return observations


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def summarize(observations: list[Observation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for endpoint in sorted({item.endpoint for item in observations}):
        group = [item for item in observations if item.endpoint == endpoint]
        success = [item for item in group if item.ok]
        scored = [item for item in success if item.cer is not None]
        critical_total = sum(item.critical_groups_total for item in success)
        critical_preserved = sum(item.critical_groups_preserved for item in success)
        inference = [item.inference_ms for item in success if item.inference_ms is not None]
        cer_units = sum(item.cer_reference_units or 0 for item in scored)
        wer_units = sum(item.wer_reference_units or 0 for item in scored)
        rtf_values = [
            item.inference_ms / 1000 / item.duration_seconds
            for item in success
            if item.inference_ms is not None and item.duration_seconds
        ]
        rows.append(
            {
                "endpoint": endpoint,
                "requests": len(group),
                "successRate": round(len(success) / len(group), 4) if group else 0.0,
                "cerMean": round(statistics.mean(item.cer for item in scored), 4) if scored else None,
                "werMean": round(statistics.mean(item.wer for item in scored), 4) if scored else None,
                "cerMicro": round(sum(item.cer_edits or 0 for item in scored) / cer_units, 4)
                if cer_units
                else None,
                "werMicro": round(sum(item.wer_edits or 0 for item in scored) / wer_units, 4)
                if wer_units
                else None,
                "criticalTermRecall": round(critical_preserved / critical_total, 4) if critical_total else None,
                "riskToSafe": sum(item.risk_to_safe for item in success),
                "nonSpeechHallucinations": sum(item.hallucinated_non_speech for item in success),
                "latencyP50Ms": _rounded(percentile((item.latency_ms for item in success), 50)),
                "latencyP95Ms": _rounded(percentile((item.latency_ms for item in success), 95)),
                "inferenceP50Ms": _rounded(percentile(inference, 50)),
                "inferenceP95Ms": _rounded(percentile(inference, 95)),
                "rtfMean": round(statistics.mean(rtf_values), 4) if rtf_values else None,
            }
        )
    return rows


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def write_results(
    output_dir: Path,
    observations: list[Observation],
    summary: list[dict[str, Any]],
    manifest: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "generatedAt": utc_now(),
        "manifest": str(manifest),
        "observations": [asdict(item) for item in observations],
    }
    (output_dir / "asr-shadow-raw.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "asr-shadow-summary.json").write_text(
        json.dumps({"generatedAt": utc_now(), "models": summary}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    if summary:
        with (output_dir / "asr-shadow-summary.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--endpoint", action="append", type=parse_endpoint, required=True)
    parser.add_argument("--api-key-env", default="ASR_API_KEY")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")
    cases = load_cases(args.manifest)
    observations = benchmark(
        cases,
        args.endpoint,
        api_key=api_key,
        runs=args.runs,
        timeout_seconds=args.timeout_seconds,
    )
    summary = summarize(observations)
    write_results(args.output_dir, observations, summary, args.manifest.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if any(row["riskToSafe"] for row in summary) else 0


if __name__ == "__main__":
    raise SystemExit(main())

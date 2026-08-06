"""동일 manifest를 캐시된 faster-whisper 모델로 측정하는 비교 기준선."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .asr_shadow_bench import (
    Case,
    Endpoint,
    benchmark,
    load_cases,
    summarize,
    write_results,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        local_files_only=True,
    )

    def invoke(
        _endpoint: Endpoint,
        case: Case,
        _api_key: str,
        _timeout: float,
        _request_id: str,
    ) -> dict:
        started = time.perf_counter()
        segments, info = model.transcribe(
            str(case.audio),
            language=case.language,
            beam_size=5,
            vad_filter=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "inference_ms": (time.perf_counter() - started) * 1000,
            "duration_seconds": info.duration,
        }

    label = f"faster-whisper-{args.model}-{args.device}-{args.compute_type}"
    observations = benchmark(
        load_cases(args.manifest),
        [Endpoint(label, "local://faster-whisper")],
        api_key="local",
        runs=args.runs,
        timeout_seconds=0,
        invoke=invoke,
    )
    summary = summarize(observations)
    write_results(args.output_dir, observations, summary, args.manifest.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 2 if any(row["riskToSafe"] for row in summary) else 0


if __name__ == "__main__":
    raise SystemExit(main())

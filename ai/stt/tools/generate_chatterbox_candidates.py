"""Chatterbox Multilingual V3 비교 후보를 사전 생성한다."""

from __future__ import annotations

import argparse
import inspect
import json
import time
from pathlib import Path

from tools.generate_tts_candidates import normalize_and_write


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    started = time.perf_counter()
    loader_parameters = inspect.signature(
        ChatterboxMultilingualTTS.from_pretrained
    ).parameters
    if "t3_model" in loader_parameters:
        model = ChatterboxMultilingualTTS.from_pretrained(
            device=torch.device("cuda"), t3_model="v3"
        )
        model_name = "ResembleAI/Chatterbox-Multilingual-V3"
    else:
        model = ChatterboxMultilingualTTS.from_pretrained(device=torch.device("cuda"))
        model_name = "ResembleAI/Chatterbox-Multilingual-V2"
    load_seconds = time.perf_counter() - started
    records = []

    for phrase in spec["phrases"]:
        for locale in spec["approvedLocales"]:
            output = args.output_dir / locale / "normal" / phrase["filename"]
            begin = time.perf_counter()
            wav = model.generate(
                phrase["texts"][locale],
                language_id="ko" if locale == "ko-KR" else "en",
            )
            metrics = normalize_and_write(wav.detach().cpu().numpy(), model.sr, output)
            records.append(
                {
                    "code": phrase["code"],
                    "locale": locale,
                    "variant": "normal",
                    "expected": phrase["texts"][locale],
                    "criticalTokens": phrase["criticalTokens"][locale],
                    "path": output.as_posix(),
                    "generationSeconds": round(time.perf_counter() - begin, 3),
                    **metrics,
                }
            )
            print(json.dumps(records[-1], ensure_ascii=False), flush=True)

    report = {
        "schemaVersion": 1,
        "model": model_name,
        "voice": "default-no-reference",
        "loadSeconds": round(load_seconds, 3),
        "peakAllocatedVramMiB": round(torch.cuda.max_memory_allocated() / 2**20),
        "records": records,
    }
    report_path = args.output_dir / "generation-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"REPORT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

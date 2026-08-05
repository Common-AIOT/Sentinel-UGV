"""승인 후보 WAV를 GPU에서 사전 생성한다. 운영 런타임에서는 사용하지 않는다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def normalize_and_write(samples, sample_rate: int, output: Path) -> dict:
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate != 16000:
        samples = resample_poly(samples, 16000, sample_rate).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms <= 0:
        raise ValueError("model produced silence")
    samples *= (10 ** (-20 / 20)) / rms
    peak = float(np.max(np.abs(samples)))
    ceiling = 10 ** (-1.5 / 20)
    if peak > ceiling:
        samples *= ceiling / peak
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, samples, 16000, subtype="PCM_16")
    return {
        "durationSeconds": round(len(samples) / 16000, 3),
        "rmsDbfs": round(float(20 * np.log10(np.sqrt(np.mean(samples**2)))), 2),
        "peakDbfs": round(float(20 * np.log10(np.max(np.abs(samples)))), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
    parser.add_argument("--speaker", default="Sohee")
    parser.add_argument(
        "--variants", nargs="+", choices=("normal", "slow"), default=["normal"]
    )
    parser.add_argument("--attn", default="sdpa")
    args = parser.parse_args()

    import torch
    from qwen_tts import Qwen3TTSModel

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        args.model,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation=args.attn,
    )
    load_seconds = time.perf_counter() - started
    records = []
    instructions = {
        "normal": spec["voice"]["normalInstruction"],
        "slow": spec["voice"]["slowInstruction"],
    }
    language_names = {"ko-KR": "Korean", "en-US": "English"}

    for phrase in spec["phrases"]:
        for locale in spec["approvedLocales"]:
            for variant in args.variants:
                output = args.output_dir / locale / variant / phrase["filename"]
                begin = time.perf_counter()
                kwargs = {
                    "text": phrase["texts"][locale],
                    "language": language_names[locale],
                    "speaker": args.speaker,
                }
                if "1.7B" in args.model:
                    kwargs["instruct"] = instructions[variant]
                wavs, sample_rate = model.generate_custom_voice(**kwargs)
                metrics = normalize_and_write(wavs[0], sample_rate, output)
                records.append(
                    {
                        "code": phrase["code"],
                        "locale": locale,
                        "variant": variant,
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
        "model": args.model,
        "speaker": args.speaker,
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

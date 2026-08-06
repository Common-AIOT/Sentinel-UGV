"""소음 manifest에서 운영 Silero VAD의 누락·오탐률을 측정한다."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from silero_vad import get_speech_timestamps, load_silero_vad

from sentinel_voice.config import VAD_OPTS
from sentinel_voice.audio import normalize

TARGET_RATE = 16_000


def load_mono_16k(path: Path) -> np.ndarray:
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if sample_rate != TARGET_RATE:
        samples = resample_poly(samples, TARGET_RATE, sample_rate)
    return np.asarray(samples, dtype=np.float32)


def benchmark(manifest: Path) -> dict:
    manifest = manifest.resolve()
    model = load_silero_vad()
    observations: list[dict] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        samples = load_mono_16k(manifest.parent / row["audio"])
        samples = normalize(samples)
        timestamps = get_speech_timestamps(
            torch.from_numpy(samples), model, sampling_rate=TARGET_RATE, **VAD_OPTS
        )
        expected_speech = bool(row.get("transcript"))
        detected = bool(timestamps)
        observations.append(
            {
                "caseId": row["caseId"],
                "condition": row.get("condition", "unknown"),
                "expectedSpeech": expected_speech,
                "detectedSpeech": detected,
                "correct": expected_speech == detected,
            }
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in observations:
        grouped[item["condition"]].append(item)
    conditions = []
    for condition in sorted(grouped):
        rows = grouped[condition]
        speech = [row for row in rows if row["expectedSpeech"]]
        non_speech = [row for row in rows if not row["expectedSpeech"]]
        conditions.append(
            {
                "condition": condition,
                "cases": len(rows),
                "speechDetected": sum(row["detectedSpeech"] for row in speech),
                "speechTotal": len(speech),
                "vadMisses": sum(not row["detectedSpeech"] for row in speech),
                "falsePositives": sum(row["detectedSpeech"] for row in non_speech),
                "nonSpeechTotal": len(non_speech),
            }
        )
    speech_all = [row for row in observations if row["expectedSpeech"]]
    non_speech_all = [row for row in observations if not row["expectedSpeech"]]
    return {
        "manifest": str(manifest),
        "vadOptions": VAD_OPTS,
        "speechDetected": sum(row["detectedSpeech"] for row in speech_all),
        "speechTotal": len(speech_all),
        "vadMisses": sum(not row["detectedSpeech"] for row in speech_all),
        "falsePositives": sum(row["detectedSpeech"] for row in non_speech_all),
        "nonSpeechTotal": len(non_speech_all),
        "conditions": conditions,
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in (
        "speechDetected", "speechTotal", "vadMisses", "falsePositives", "nonSpeechTotal"
    )}, ensure_ascii=False))
    return 2 if result["vadMisses"] or result["falsePositives"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

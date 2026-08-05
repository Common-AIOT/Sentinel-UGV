"""승인 TTS 후보에 재현 가능한 구조 로봇 근사 소음을 SNR 기준으로 섞는다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf


def mix_motor_noise(
    samples: np.ndarray,
    sample_rate: int,
    snr_db: float,
    *,
    seed: int = 271,
) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    signal_rms = float(np.sqrt(np.mean(samples**2)))
    if signal_rms <= 0:
        raise ValueError("cannot mix noise into silence")
    rng = np.random.default_rng(seed)
    t = np.arange(len(samples), dtype=np.float32) / sample_rate
    noise = (
        0.55 * np.sin(2 * np.pi * 120 * t)
        + 0.25 * np.sin(2 * np.pi * 240 * t)
        + 0.20 * rng.standard_normal(len(samples))
    ).astype(np.float32)
    noise_rms = float(np.sqrt(np.mean(noise**2)))
    target_noise_rms = signal_rms / (10 ** (snr_db / 20))
    mixed = samples + noise * (target_noise_rms / noise_rms)
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.89:
        mixed *= 0.89 / peak
    return mixed.astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="generation-report.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snr-db", type=float, default=5.0)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    records = []
    base = args.input.parent.parent
    for index, record in enumerate(payload["records"]):
        source = base / record["path"]
        samples, sample_rate = sf.read(source, dtype="float32")
        mixed = mix_motor_noise(samples, sample_rate, args.snr_db, seed=271 + index)
        relative = Path(record["locale"]) / record["variant"] / source.name
        output = args.output_dir / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output, mixed, sample_rate, subtype="PCM_16")
        noisy_record = dict(record)
        noisy_record.update(
            {
                "path": output.as_posix(),
                "noise": "motor-harmonics-plus-white",
                "snrDb": args.snr_db,
            }
        )
        records.append(noisy_record)

    report = {
        "schemaVersion": 1,
        "sourceModel": payload["model"],
        "noise": "motor-harmonics-plus-white",
        "snrDb": args.snr_db,
        "records": records,
    }
    report_path = args.output_dir / "generation-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

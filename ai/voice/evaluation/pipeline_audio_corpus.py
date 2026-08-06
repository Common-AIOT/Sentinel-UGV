"""평가 manifest의 WAV를 실제 Voice 입력과 같은 16kHz mono/RMS로 변환한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import soundfile as sf

from sentinel_voice.audio import normalize

from .vad_noise_bench import TARGET_RATE, load_mono_16k


def build_pipeline_corpus(manifest: Path, output_dir: Path) -> Path:
    manifest = manifest.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source = (manifest.parent / row["audio"]).resolve()
        samples = normalize(load_mono_16k(source))
        target = output_dir / Path(row["audio"])
        target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(target, samples, TARGET_RATE, subtype="PCM_16")
        copied = dict(row)
        copied["audio"] = target.relative_to(output_dir).as_posix()
        copied["pipelineFormat"] = {
            "sampleRate": TARGET_RATE,
            "channels": 1,
            "targetRms": 0.08,
        }
        rows.append(copied)
    output_manifest = output_dir / "manifest.jsonl"
    output_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output_dir / "corpus-metadata.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "sourceManifest": str(manifest),
                "cases": len(rows),
                "sampleRate": TARGET_RATE,
                "channels": 1,
                "targetRms": 0.08,
                "purpose": "mirror sentinel_voice audio normalization before VAD/ASR",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_pipeline_corpus(args.manifest, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

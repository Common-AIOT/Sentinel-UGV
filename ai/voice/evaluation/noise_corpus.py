"""기존 정답 ASR fixture로 재현 가능한 합성 소음 코퍼스를 만든다.

원본은 읽기만 하고 출력 디렉터리에 clean 복사본과 파생 WAV/manifest를 만든다.
합성 결과는 재난 현장 실측의 대체물이 아니라 회귀·튜닝 전 단계의 스트레스 시험이다.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

PROFILES = ("motor-fan", "alarm", "rubble")
DEFAULT_SNRS = (10.0, 5.0, 0.0)


def rms(samples: np.ndarray) -> float:
    samples = np.asarray(samples, dtype=np.float64)
    return float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0


def _colored_noise(rng: np.random.Generator, size: int) -> np.ndarray:
    white = rng.standard_normal(size)
    # 팬·현장 배경음을 흰소음 하나로만 만들지 않도록 간단한 저역 누적 필터를 쓴다.
    colored = np.empty(size, dtype=np.float64)
    previous = 0.0
    for index, value in enumerate(white):
        previous = 0.92 * previous + 0.08 * value
        colored[index] = previous
    return colored


def synthesize_noise(
    profile: str, size: int, sample_rate: int, *, seed: int
) -> np.ndarray:
    if profile not in PROFILES:
        raise ValueError(f"unknown noise profile: {profile}")
    rng = np.random.default_rng(seed)
    t = np.arange(size, dtype=np.float64) / sample_rate
    colored = _colored_noise(rng, size)

    if profile == "motor-fan":
        noise = (
            0.55 * np.sin(2 * np.pi * 90 * t)
            + 0.25 * np.sin(2 * np.pi * 180 * t)
            + 0.20 * colored
        )
    elif profile == "alarm":
        sweep = 850 + 230 * np.sin(2 * np.pi * 0.7 * t)
        phase = 2 * np.pi * np.cumsum(sweep) / sample_rate
        pulse = (np.sin(2 * np.pi * 1.2 * t) > -0.15).astype(np.float64)
        noise = 0.82 * np.sin(phase) * pulse + 0.18 * colored
    else:  # rubble
        impulses = np.zeros(size, dtype=np.float64)
        count = max(2, math.ceil(size / sample_rate * 3))
        positions = rng.integers(0, max(size, 1), size=count)
        impulses[positions] = rng.uniform(-4.0, 4.0, size=count)
        kernel_size = max(8, int(sample_rate * 0.025))
        kernel = np.exp(-np.arange(kernel_size) / max(1, sample_rate * 0.004))
        impacts = np.convolve(impulses, kernel, mode="full")[:size]
        noise = 0.55 * colored + 0.45 * impacts

    level = rms(noise)
    if level <= 0:
        raise ValueError("generated noise is silent")
    return (noise / level).astype(np.float32)


def mix_at_snr(
    speech: np.ndarray, noise: np.ndarray, snr_db: float
) -> np.ndarray:
    return mix_components_at_snr(speech, noise, snr_db)[2]


def mix_components_at_snr(
    speech: np.ndarray, noise: np.ndarray, snr_db: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """최종 혼합에 실제 사용된 음성과 소음 성분을 함께 반환한다."""

    speech = np.asarray(speech, dtype=np.float32).reshape(-1)
    noise = np.asarray(noise, dtype=np.float32).reshape(-1)
    if speech.size != noise.size:
        raise ValueError("speech and noise must have the same length")
    speech_rms = rms(speech)
    if speech_rms <= 0:
        raise ValueError("cannot mix noise into silence")
    noise_rms = rms(noise)
    target_noise_rms = speech_rms / (10 ** (snr_db / 20))
    noise_component = noise * (target_noise_rms / noise_rms)
    speech_component = speech.copy()
    mixed = speech_component + noise_component
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.95:
        gain = 0.95 / peak
        speech_component *= gain
        noise_component *= gain
        mixed *= gain
    return (
        speech_component.astype(np.float32),
        noise_component.astype(np.float32),
        mixed.astype(np.float32),
    )


def _read_manifest(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _domain_annotations(row: dict) -> dict:
    """record_corpus manifest의 질문·정답을 ASR 안전 평가 필드로 바꾼다."""

    text = str(row.get("text", ""))
    question = str(row.get("question", ""))
    expected = row.get("expectedValue")
    groups: list[list[str]] = []
    if question == "INTRO":
        groups = [[token] for token in ("네", "사람", "도와") if token in text]
    elif question == "URGENT":
        if expected == "NO":
            groups = [["없"]]
        elif "다리" in text:
            groups = [["다리"], ["다쳤", "다친"]]
        elif "피" in text:
            groups = [["피"], ["나"]]
        elif "숨" in text:
            groups = [["숨쉬", "호흡"], ["힘들"]]
    elif question == "MOBILITY":
        if "혼자" in text:
            groups = [["혼자"], ["걸"], ["있", "가능"]]
        elif "눌" in text:
            groups = [["눌"], ["못"], ["움직"]]
        elif "일어나" in text:
            groups = [["일어나"], ["아파"]]
        else:
            groups = [["움직"], ["있", "가능"]]
    elif question == "COUNT":
        if "혼자" in text:
            groups = [["혼자"]]
        elif "세 명" in text:
            groups = [["세 명", "3명"], ["포함"]]
        elif "두 명" in text:
            groups = [["두 명", "2명"], ["더"]]
        else:
            groups = [["한 명", "1명"], ["대답"], ["안"]]
    elif "가스" in text:
        groups = [["가스"], ["냄새"]]

    polarity = "unknown"
    risk_patterns: list[str] = []
    safe_patterns: list[str] = []
    if question == "URGENT":
        polarity = "risk" if expected == "YES" else "safe"
        risk_patterns = ["다쳤", "피가", "숨쉬기가 힘들", "아파"]
        safe_patterns = ["다친 곳은 없", "안 다쳤"]
    elif question == "MOBILITY":
        polarity = "risk" if expected == "NO" else "safe"
        risk_patterns = ["못 움직", "움직일 수 없", "일어나려니까 너무 아파"]
        safe_patterns = ["움직일 수 있", "걸을 수 있"]
    return {
        "criticalTermGroups": groups,
        "expectedPolarity": polarity,
        "riskPatterns": risk_patterns,
        "safePatterns": safe_patterns,
    }


def _normalize_source_row(row: dict) -> dict:
    if "audio" in row:
        return dict(row)
    if "file" not in row or "text" not in row:
        raise ValueError("manifest row requires audio/transcript or file/text")
    expected_field = row.get("expectedField")
    expected_value = row.get("expectedValue")
    # 원본 라벨은 보존하되, 이후 확정된 COUNT 계약은 파생 manifest에 반영한다.
    if row.get("text") == "두 명 더 있어요":
        expected_field = "reportedResponsiveCount"
        expected_value = 1
    elif row.get("text") == "옆에 한 명 있는데 대답을 안 해요":
        expected_field = "reportedResponsiveCount"
        expected_value = 1
    normalized = {
        "caseId": f"domain-{int(row.get('lineNumber', 0)):02d}",
        "audio": row["file"],
        "transcript": row["text"],
        "language": "ko",
        "condition": row.get("condition", "clean"),
        "expectedField": expected_field,
        "expectedValue": expected_value,
        "question": row.get("question"),
    }
    normalized.update(_domain_annotations(row))
    return normalized


def _load_external_noise(
    path: Path, sample_rate: int, size: int, *, seed: int
) -> np.ndarray:
    samples, source_rate = sf.read(path, dtype="float32", always_2d=False)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if source_rate != sample_rate:
        samples = resample_poly(samples, sample_rate, source_rate).astype(np.float32)
    if not len(samples):
        raise ValueError(f"noise file is empty: {path}")
    if len(samples) < size:
        samples = np.tile(samples, math.ceil(size / len(samples)))
    max_start = len(samples) - size
    start = (
        int(np.random.default_rng(seed).integers(0, max_start + 1))
        if max_start > 0
        else 0
    )
    selected = np.asarray(samples[start : start + size], dtype=np.float32)
    if rms(selected) <= 0:
        raise ValueError(f"selected noise segment is silent: {path}")
    return selected


def build_corpus(
    manifest: Path,
    output_dir: Path,
    *,
    language: str = "ko",
    snrs: tuple[float, ...] = DEFAULT_SNRS,
    profiles: tuple[str, ...] = PROFILES,
    noise_files: tuple[Path, ...] = (),
    seed: int = 303,
) -> Path:
    manifest = manifest.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    source_rows = [_normalize_source_row(row) for row in _read_manifest(manifest)]
    selected_profiles = (
        tuple(path.stem for path in noise_files) if noise_files else profiles
    )
    for source_index, row in enumerate(source_rows):
        if row.get("language") != language:
            continue
        condition = str(row.get("condition", ""))
        source = (manifest.parent / row["audio"]).resolve()
        if condition in {"silence", "noise-only"}:
            target = output_dir / "non-speech" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied = dict(row)
            copied["audio"] = target.relative_to(output_dir).as_posix()
            rows.append(copied)
            continue
        if not row.get("transcript"):
            continue

        samples, sample_rate = sf.read(source, dtype="float32", always_2d=False)
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        clean_target = output_dir / "clean" / source.name
        clean_target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(clean_target, samples, sample_rate, subtype="PCM_16")
        clean = dict(row)
        clean.update(
            {
                "caseId": f"{row['caseId']}--clean",
                "audio": clean_target.relative_to(output_dir).as_posix(),
                "condition": "clean",
                "noiseProfile": None,
                "snrDb": None,
                "sourceCaseId": row["caseId"],
            }
        )
        rows.append(clean)

        for profile_index, profile in enumerate(selected_profiles):
            if noise_files:
                noise = _load_external_noise(
                    noise_files[profile_index],
                    sample_rate,
                    len(samples),
                    seed=seed + source_index * 100 + profile_index,
                )
            else:
                noise = synthesize_noise(
                    profile,
                    len(samples),
                    sample_rate,
                    seed=seed + source_index * 100 + profile_index,
                )
            for snr_db in snrs:
                speech_component, noise_component, mixed = mix_components_at_snr(
                    samples, noise, snr_db
                )
                snr_label = f"{snr_db:g}db"
                target = output_dir / profile / snr_label / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                sf.write(target, mixed, sample_rate, subtype="PCM_16")
                # 청취 검수와 재현성 확인용이다. 혼합 파일에 실제 더한 것과 같은
                # 길이·레벨·seed의 소음 성분이며 ASR manifest에는 넣지 않는다.
                noise_target = (
                    output_dir
                    / "noise-components"
                    / profile
                    / snr_label
                    / source.name
                )
                noise_target.parent.mkdir(parents=True, exist_ok=True)
                sf.write(
                    noise_target,
                    noise_component,
                    sample_rate,
                    subtype="PCM_16",
                )
                speech_target = (
                    output_dir
                    / "speech-components"
                    / profile
                    / snr_label
                    / source.name
                )
                speech_target.parent.mkdir(parents=True, exist_ok=True)
                sf.write(
                    speech_target,
                    speech_component,
                    sample_rate,
                    subtype="PCM_16",
                )
                noisy = dict(row)
                noisy.update(
                    {
                        "caseId": f"{row['caseId']}--{profile}--{snr_label}",
                        "audio": target.relative_to(output_dir).as_posix(),
                        "condition": f"{profile}@{snr_label}",
                        "noiseProfile": profile,
                        "snrDb": snr_db,
                        "sourceCaseId": row["caseId"],
                    }
                )
                rows.append(noisy)

    if noise_files:
        # 실제 소음만 들어간 파일도 동일 길이로 잘라 VAD 오탐·ASR 환각을 측정한다.
        sample_rate = 48_000
        size = sample_rate * 6
        for profile_index, path in enumerate(noise_files):
            profile = path.stem
            samples = _load_external_noise(
                path, sample_rate, size, seed=seed + 10_000 + profile_index
            )
            target = output_dir / "non-speech" / f"{profile}.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            sf.write(target, samples, sample_rate, subtype="PCM_16")
            rows.append(
                {
                    "caseId": f"noise-only--{profile}",
                    "audio": target.relative_to(output_dir).as_posix(),
                    "transcript": "",
                    "language": language,
                    "condition": f"noise-only-{profile}",
                    "criticalTermGroups": [],
                    "expectedPolarity": "unknown",
                    "riskPatterns": [],
                    "safePatterns": [],
                    "noiseProfile": profile,
                    "snrDb": None,
                }
            )

    output_manifest = output_dir / "manifest.jsonl"
    output_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    metadata = {
        "schemaVersion": 1,
        "sourceManifest": str(manifest),
        "language": language,
        "profiles": list(selected_profiles),
        "noiseSources": [str(path.resolve()) for path in noise_files],
        "snrDb": list(snrs),
        "cases": len(rows),
        "noiseComponents": "noise-components/<profile>/<snr>/<source wav>",
        "speechComponents": "speech-components/<profile>/<snr>/<source wav>",
        "fieldApproval": False,
        "limitation": (
            "human speech plus recorded noise with deterministic digital SNR mixing; "
            "not simultaneous acoustic field recording"
            if noise_files
            else "deterministic synthetic stress corpus; not a field recording"
        ),
    }
    (output_dir / "corpus-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language", default="ko")
    parser.add_argument("--snr", action="append", type=float, dest="snrs")
    parser.add_argument("--profile", action="append", choices=PROFILES, dest="profiles")
    parser.add_argument(
        "--noise-dir",
        type=Path,
        help="합성 profile 대신 이 폴더의 실제 WAV를 모두 사용한다",
    )
    parser.add_argument("--seed", type=int, default=303)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    noise_files = (
        tuple(sorted(args.noise_dir.resolve().glob("*.wav")))
        if args.noise_dir
        else ()
    )
    if args.noise_dir and not noise_files:
        raise SystemExit(f"no WAV files in --noise-dir: {args.noise_dir}")
    output = build_corpus(
        args.manifest,
        args.output_dir,
        language=args.language,
        snrs=tuple(args.snrs or DEFAULT_SNRS),
        profiles=tuple(args.profiles or PROFILES),
        noise_files=noise_files,
        seed=args.seed,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

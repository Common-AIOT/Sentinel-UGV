"""MiniMax 원본 WAV를 Jetson 안내 음성 규격으로 일괄 변환한다.

입력 예:
    mini_intro.wav
    mini_ask_count.wav

출력 예:
    assets/guide_intro.wav
    assets/guide_ask_count.wav
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from sentinel_voice import config
from sentinel_voice.guide_audio import GUIDE_ASSETS, validate_wav


def source_filename(output_filename: str) -> str:
    """guide_xxx.wav에 대응하는 MiniMax 원본명 mini_xxx.wav를 반환한다."""
    if not output_filename.startswith("guide_"):
        raise ValueError(f"안내 파일명 규칙 위반: {output_filename}")
    return f"mini_{output_filename.removeprefix('guide_')}"


def convert_one(
    source: Path, output: Path, *, ffmpeg: str, force: bool
) -> None:
    if output.exists() and not force:
        raise FileExistsError(
            f"{output} 파일이 이미 있음. 교체하려면 --force를 사용하세요."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y" if force else "-n",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-af",
        "loudnorm=I=-20:TP=-2:LRA=7",
        "-ac",
        "1",
        "-ar",
        str(config.FS),
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=config.STT_ROOT,
        help="mini_*.wav가 있는 폴더(기본: ai/stt)",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=config.STT_ROOT / "assets",
        help="변환된 guide_*.wav 출력 폴더",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 assets/guide_*.wav를 새 파일로 교체",
    )
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(
            "[FAIL] ffmpeg를 찾을 수 없습니다. "
            "conda install -c conda-forge ffmpeg -y"
        )
        return 1

    failures = 0
    for code, asset in GUIDE_ASSETS.items():
        source = args.source_dir / source_filename(asset.filename)
        output = args.assets_dir / asset.filename

        if not source.is_file():
            print(f"[FAIL] {code.value:<29} 원본 없음: {source}")
            failures += 1
            continue

        try:
            convert_one(source, output, ffmpeg=ffmpeg, force=args.force)
            inspection = validate_wav(output)
            print(
                f"[OK]   {code.value:<29} {source.name} -> "
                f"{output.name} "
                f"({inspection.duration_seconds:.2f}s, "
                f"RMS={inspection.rms_dbfs:.1f}dBFS)"
            )
        except (
            FileExistsError,
            OSError,
            ValueError,
            subprocess.CalledProcessError,
        ) as exc:
            print(f"[FAIL] {code.value:<29} {type(exc).__name__}: {exc}")
            failures += 1

    if failures:
        print(f"\nFAIL {failures}개 - 원본명과 변환 오류를 확인하세요.")
        return 1

    print(f"\nOK - 안내 음성 {len(GUIDE_ASSETS)}개 변환·검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

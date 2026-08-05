"""배포 전 사전녹음 안내 WAV 전체 검사."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from sentinel_voice import config
from sentinel_voice.guide_audio import GUIDE_ASSETS, validate_wav


def validate_assets(assets_dir: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    failures = 0
    for code, asset in GUIDE_ASSETS.items():
        path = assets_dir / asset.filename
        row = {
            "code": code.value,
            "filename": asset.filename,
            "text": asset.text,
            "status": "FAIL",
        }
        if not path.is_file():
            row["error"] = "FILE_NOT_FOUND"
            failures += 1
        else:
            try:
                row.update(asdict(validate_wav(path)))
                row["status"] = "OK"
            except (OSError, ValueError) as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                failures += 1
        rows.append(row)
    return rows, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--assets-dir", type=Path, default=config.VOICE_ROOT / "assets"
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    rows, failures = validate_assets(args.assets_dir)
    for row in rows:
        detail = (
            row["error"]
            if "error" in row
            else f"RMS={row['rms_dbfs']:.1f}dBFS"
        )
        print(
            f"[{row['status']}] {row['code']:<24} "
            f"{row['filename']} - {detail}"
        )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"보고서: {args.report.resolve()}")

    if failures:
        print(f"\nFAIL {failures}개 - 배포 전에 수정하세요.")
        return 1
    print(f"\nOK - 안내 음성 {len(rows)}개 검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

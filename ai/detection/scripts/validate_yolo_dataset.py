"""YOLO 학습 데이터 검증. AGENTS.md §12 규칙을 그대로 구현한다.

학습을 돌리기 전에 라벨이 깨지지 않았는지 확인한다. 좌표가 0~1을 벗어나거나
이미지·라벨 짝이 어긋난 채로 학습하면 손실은 내려가는데 성능이 안 나오고,
원인을 찾는 데 시간이 오래 걸린다.

오류가 있으면 exit code 1, 정상이면 0을 낸다(CI에서 쓸 수 있게).

사용 예:
    python scripts/validate_yolo_dataset.py --root data/processed
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

MAX_REPORT = 10


def check_split(root: Path, split: str, num_classes: int) -> tuple[int, list[str]]:
    """한 split을 검사하고 (이미지 수, 오류 목록)을 돌려준다."""
    img_dir = root / "images" / split
    lbl_dir = root / "labels" / split
    errors: list[str] = []

    if not img_dir.is_dir():
        return 0, [f"{split}: images 디렉터리 없음 ({img_dir})"]
    if not lbl_dir.is_dir():
        return 0, [f"{split}: labels 디렉터리 없음 ({lbl_dir})"]

    images = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    labels = {p.stem for p in lbl_dir.glob("*.txt")}

    # 짝 확인 — 양방향으로 본다. 라벨만 남으면 학습에서 조용히 무시된다.
    for img in images:
        if img.stem not in labels:
            errors.append(f"{split}: 라벨 없는 이미지 {img.name}")
    orphan = labels - {p.stem for p in images}
    for stem in sorted(orphan):
        errors.append(f"{split}: 이미지 없는 라벨 {stem}.txt")

    for lbl in sorted(lbl_dir.glob("*.txt")):
        for lineno, line in enumerate(lbl.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            where = f"{split}/{lbl.name}:{lineno}"
            if len(parts) != 5:
                errors.append(f"{where}: 값이 5개가 아님({len(parts)}개)")
                continue
            try:
                cid = int(parts[0])
                x, y, w, h = (float(v) for v in parts[1:])
            except ValueError:
                errors.append(f"{where}: 숫자로 읽을 수 없음 — {line}")
                continue
            if not 0 <= cid < num_classes:
                errors.append(f"{where}: class id {cid}가 범위(0~{num_classes-1}) 밖")
            for name, v in (("x", x), ("y", y), ("w", w), ("h", h)):
                if not 0.0 <= v <= 1.0:
                    errors.append(f"{where}: {name}={v}가 0~1 밖")
            if w <= 0 or h <= 0:
                errors.append(f"{where}: w/h가 0 이하 ({w}, {h})")

    return len(images), errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path("data/processed"))
    ap.add_argument("--splits", nargs="+", default=["train", "val"])
    ap.add_argument("--num-classes", type=int, default=1)
    args = ap.parse_args()

    total_errors: list[str] = []
    counts = Counter()
    for split in args.splits:
        n, errors = check_split(args.root, split, args.num_classes)
        counts[split] = n
        total_errors.extend(errors)
        mark = "OK" if not errors else f"오류 {len(errors)}건"
        print(f"{split:6s} 이미지 {n:6d}장  {mark}")

    if total_errors:
        print(f"\n총 오류 {len(total_errors)}건 (최대 {MAX_REPORT}건 표시)")
        for e in total_errors[:MAX_REPORT]:
            print(f"  - {e}")
        if len(total_errors) > MAX_REPORT:
            print(f"  ... 외 {len(total_errors) - MAX_REPORT}건")
        return 1

    print(f"\n정상. 합계 {sum(counts.values())}장")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

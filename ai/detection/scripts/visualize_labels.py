"""YOLO 라벨을 이미지에 그려 눈으로 확인한다. AGENTS.md §12 규칙을 따른다.

`validate_yolo_dataset.py`는 값이 형식에 맞는지만 본다. 좌표가 0~1 안에 있어도
박스가 사람을 안 감싸면 학습이 망가지는데, 그건 **그려봐야 안다.**

특히 71550은 관절에서 bbox를 유도하므로(`convert_71550.py`), 관절 바깥 여유
(`--margin`)가 적절한지 확인하는 것이 이 스크립트의 주 목적이다. 관절은 몸의 끝이
아니라서 정수리·손끝·발끝·옷이 박스 밖으로 나간다.

**원본을 수정하지 않는다.** 결과는 data/samples에 따로 쓴다.

사용 예:
    python scripts/visualize_labels.py --root data/processed --split train -n 12

    # 여유값을 바꿔가며 비교할 때는 변환을 다시 돌린 뒤 다른 폴더로 뽑는다
    python scripts/visualize_labels.py --root data/processed --out data/samples/m20
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import cv2

BOX_COLOR = (0, 220, 0)
TEXT_COLOR = (0, 0, 0)


def draw(img, line: str) -> tuple[float, float] | None:
    """YOLO 한 줄을 픽셀 좌표로 되돌려 그린다. 반환은 (가로/세로비, 넓이비)."""
    parts = line.split()
    if len(parts) != 5:
        return None
    cid = parts[0]
    x, y, w, h = (float(v) for v in parts[1:])
    ih, iw = img.shape[:2]

    bw, bh = w * iw, h * ih
    x1 = int((x * iw) - bw / 2)
    y1 = int((y * ih) - bh / 2)
    x2, y2 = int(x1 + bw), int(y1 + bh)

    cv2.rectangle(img, (x1, y1), (x2, y2), BOX_COLOR, 3)
    label = f"{cid} {bw/max(bh,1e-6):.2f}"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.rectangle(img, (x1, y1 - th - 8), (x1 + tw + 6, y1), BOX_COLOR, -1)
    cv2.putText(img, label, (x1 + 3, y1 - 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT_COLOR, 2)
    return bw / max(bh, 1e-6), (bw * bh) / (iw * ih)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path("data/processed"))
    ap.add_argument("--split", default="train")
    ap.add_argument("--out", type=Path, default=None,
                    help="기본은 data/samples/<split>")
    ap.add_argument("-n", "--num", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42, help="샘플 고정용")
    ap.add_argument("--max-width", type=int, default=1280,
                    help="보기 편하게 축소한다. 0이면 원본 크기")
    args = ap.parse_args()

    img_dir = args.root / "images" / args.split
    lbl_dir = args.root / "labels" / args.split
    if not img_dir.is_dir():
        print(f"이미지 디렉터리가 없다: {img_dir}", file=sys.stderr)
        return 1

    images = sorted(p for p in img_dir.iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if not images:
        print(f"이미지가 없다: {img_dir}", file=sys.stderr)
        return 1

    out_dir = args.out or (Path("data/samples") / args.split)
    out_dir.mkdir(parents=True, exist_ok=True)

    picked = random.Random(args.seed).sample(images, min(args.num, len(images)))
    aspects: list[float] = []
    areas: list[float] = []
    drawn = 0

    for img_path in picked:
        lbl_path = lbl_dir / f"{img_path.stem}.txt"
        if not lbl_path.exists():
            print(f"라벨 없음, 건너뜀: {img_path.name}", file=sys.stderr)
            continue
        img = cv2.imread(str(img_path))
        if img is None:
            print(f"읽기 실패, 건너뜀: {img_path.name}", file=sys.stderr)
            continue

        for line in lbl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            got = draw(img, line)
            if got:
                aspects.append(got[0])
                areas.append(got[1])

        if args.max_width and img.shape[1] > args.max_width:
            scale = args.max_width / img.shape[1]
            img = cv2.resize(img, (args.max_width, int(img.shape[0] * scale)))
        cv2.imwrite(str(out_dir / f"{img_path.stem}.jpg"), img)
        drawn += 1

    print(f"{drawn}장 저장 → {out_dir}  (seed {args.seed})")
    if aspects:
        a = sorted(aspects)
        ar = sorted(areas)
        print(f"박스 가로/세로비  중앙 {a[len(a)//2]:.2f}  "
              f"min {a[0]:.2f}  max {a[-1]:.2f}   (1.0 넘으면 가로로 긴 = 누운 형태)")
        print(f"박스 화면 점유율  중앙 {ar[len(ar)//2]*100:.1f}%")
    print("\n박스가 사람을 감싸는지 눈으로 확인한다. "
          "좁으면 convert_71550.py --margin 을 올린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

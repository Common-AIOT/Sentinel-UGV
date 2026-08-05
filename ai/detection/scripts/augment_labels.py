"""라벨에 빠진 사람을 채운다. 이미 만들어진 데이터셋의 **라벨만** 다시 쓴다.

## 왜 필요한가

AI-Hub 71550의 전도 라벨은 **쓰러지는 사람 한 명만** 표시한다. 같은 화면에 있는
점원이나 다른 손님은 라벨이 없다.

YOLO는 라벨이 없는 영역을 **배경으로 학습한다.** 따라서 그대로 학습하면
"서 있는 사람 = 배경"이라고 34,488장 내내 가르치게 된다. 실제로 그렇게 학습한
모델은 서 있는 사람 탐지가 100% -> 24% 이하로 무너졌다(2026-08-04 A/B).

서 있는 사람 데이터를 안 섞어서 생긴 문제가 아니라, 화면 안에 있는 서 있는 사람을
**적극적으로 배경이라고 가르쳐서** 생긴 문제다.

## 왜 사전학습 모델의 출력을 라벨로 쓰는가

일반적으로 모델 출력을 라벨로 재사용하면(pseudo-labeling) 모르는 것은 영원히
모르게 된다. 그래서 **누운 사람을 가르치는 데는 쓸 수 없다.**

여기서는 목적이 반대다. 사전학습 모델은 서 있는 사람을 99~100% 잡는다. 그 출력을
라벨로 쓰는 것은 새 지식을 가르치는 게 아니라 **이미 아는 것을 잊지 말라고
붙잡아두는 것**이다(망각 방지).

⚠️ **이 라벨은 정답이 아니다.** 사전학습 모델이 놓친 사람은 여전히 배경으로 남는다.
완전한 해결이 아니라 덜 틀리게 만드는 것이다. 결과를 "검증됨"으로 보고하지 않는다.

관절에서 유도한 원래 박스는 사람이 라벨링한 정답이므로 **항상 보존**하고, 그와
겹치지 않는 탐지만 추가한다.

사용 예:
    # 먼저 몇 장으로 확인
    python scripts/augment_labels.py --root data/processed --split val --limit 200

    # 전체 적용 (원본 라벨은 .orig 로 백업된다)
    python scripts/augment_labels.py --root data/processed --split train
    python scripts/augment_labels.py --root data/processed --split val

    # 되돌리기
    python scripts/augment_labels.py --root data/processed --split train --restore
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKUP_SUFFIX = ".orig"
PERSON_CLASS_ID = 0


def to_xyxy(line: str, w: int, h: int) -> tuple[float, float, float, float] | None:
    parts = line.split()
    if len(parts) != 5:
        return None
    _, x, y, bw, bh = parts
    x, y, bw, bh = float(x) * w, float(y) * h, float(bw) * w, float(bh) * h
    return (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)


def iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path("data/processed"))
    ap.add_argument("--split", default="train")
    ap.add_argument("--model", default="models/yolo26n.pt",
                    help="라벨을 보강할 사전학습 모델. 파인튜닝 가중치를 쓰지 않는다")
    ap.add_argument("--conf", type=float, default=0.40,
                    help="낮출수록 사람을 더 찾지만 오탐도 늘어난다. "
                         "배경으로 잘못 가르치는 쪽이 더 위험하므로 기본값을 낮게 둔다")
    ap.add_argument("--iou", type=float, default=0.40,
                    help="정답 박스와 이만큼 겹치면 같은 사람으로 보고 추가하지 않는다")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--restore", action="store_true", help="백업에서 되돌린다")
    args = ap.parse_args()

    lbl_dir = args.root / "labels" / args.split
    img_dir = args.root / "images" / args.split
    if not lbl_dir.is_dir():
        print(f"라벨 디렉터리가 없다: {lbl_dir}", file=sys.stderr)
        return 1

    if args.restore:
        n = 0
        for bak in lbl_dir.glob(f"*.txt{BACKUP_SUFFIX}"):
            target = bak.with_suffix("")
            target.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            bak.unlink()
            n += 1
        print(f"{n}개 라벨을 원래대로 되돌렸다")
        return 0

    labels = sorted(lbl_dir.glob("*.txt"))
    if args.limit:
        labels = labels[: args.limit]
    if not labels:
        print(f"라벨이 없다: {lbl_dir}", file=sys.stderr)
        return 1

    import cv2
    from ultralytics import YOLO

    model = YOLO(args.model)
    added_total = 0
    frames_with_add = 0
    processed = 0

    for start in range(0, len(labels), args.batch):
        chunk = labels[start: start + args.batch]
        imgs, metas = [], []
        for lp in chunk:
            ip = img_dir / f"{lp.stem}.jpg"
            if not ip.exists():
                continue
            im = cv2.imread(str(ip))
            if im is None:
                continue
            imgs.append(im)
            metas.append((lp, im.shape[1], im.shape[0]))
        if not imgs:
            continue

        results = model.predict(imgs, classes=[0], conf=args.conf, verbose=False)

        for (lp, w, h), res in zip(metas, results):
            # 원본은 한 번만 백업한다. 두 번 돌려도 정답이 보존된다.
            bak = lp.with_suffix(lp.suffix + BACKUP_SUFFIX)
            if not bak.exists():
                bak.write_text(lp.read_text(encoding="utf-8"), encoding="utf-8")
            base_lines = [ln for ln in bak.read_text(encoding="utf-8").splitlines() if ln.strip()]
            gt = [b for b in (to_xyxy(ln, w, h) for ln in base_lines) if b]

            extra = []
            for box in res.boxes.xyxy.tolist():
                if any(iou(box, g) >= args.iou for g in gt):
                    continue
                if any(iou(box, e) >= args.iou for e in extra):
                    continue
                extra.append(tuple(box))

            if extra:
                frames_with_add += 1
                added_total += len(extra)
                lines = list(base_lines)
                for x1, y1, x2, y2 in extra:
                    cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
                    bw, bh = (x2 - x1) / w, (y2 - y1) / h
                    lines.append(
                        f"{PERSON_CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
                lp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            processed += 1

        if start and start % (args.batch * 50) == 0:
            print(f"  {processed}/{len(labels)}장 · 추가 {added_total}개", flush=True)

    print(f"\n{args.split}: {processed}장 처리")
    print(f"  사람이 추가된 프레임 {frames_with_add}장 "
          f"({frames_with_add / max(processed, 1) * 100:.1f}%)")
    print(f"  추가된 박스 {added_total}개 "
          f"(프레임당 평균 {added_total / max(processed, 1):.2f}개)")
    print(f"\n원본은 *.txt{BACKUP_SUFFIX} 로 백업됐다. --restore 로 되돌린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

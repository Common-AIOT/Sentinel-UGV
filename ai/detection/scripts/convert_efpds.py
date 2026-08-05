"""E-FPDS(Extended Fallen Person Dataset) → YOLO 학습 데이터 변환.

## 왜 이 데이터인가

71550(실내 편의점)만으로 두 번 파인튜닝했고 두 번 다 기각됐다. 원인은 매번 같다 —
**배경이 편의점 한 곳뿐**이라 모델이 자세가 아니라 장면을 외운다.

    1차: 서 있는 사람 100% -> 24%,  99% -> 0%
    2차: 평균 69.2% -> 30.1%  (라벨 보강 + 서 있는 사람 25,540장 추가 후)

E-FPDS는 그 결핍을 정면으로 메운다.

| | 71550 | E-FPDS |
|---|---|---|
| 배경 | 편의점 1곳 | **12개 split(장소)** |
| 카메라 | 매장 CCTV, 내려다봄 | **바닥 76cm, 이동 로봇** |
| 비쓰러짐 | 없음(직접 생성) | **2,275개 정답** |

**카메라 높이 76cm에 이동 로봇 탑재는 우리 UGV와 사실상 같은 시점이다.**
즉 이건 "다양성 보강용 추가 데이터"가 아니라 **우리 배치 도메인 데이터**다.

## 라벨 형식 — YOLO가 아니다

    class left right top bot     (절대 픽셀, x가 먼저 둘 / y가 나중 둘)

x/y가 번갈아 나오는 xyxy가 아니라 **x 두 개 뒤에 y 두 개**다. 저자가 공개한
annotations.py로 확인했다(gram.web.uah.es/data/datasets/fpds/annotations.py).
추측했으면 뒤집혔을 자리다.

클래스는 `1`=쓰러짐, `-1`=비쓰러짐(서 있음·앉음·소파에 누움·걷기), `0`=기본값.
**우리는 person 단일 클래스이므로 셋 다 0으로 합친다.** 자세 판정은 학습이 아니라
posture_classifier.py의 규칙이 한다(AGENTS.md §10).

## split은 장소 단위로 이미 갈려 있다

    train  split1,2,3,10,11      valid  split12,13      test  split4~8

저자가 **장소가 겹치지 않게** 나눠뒀다. 우리가 다시 섞으면 그 설계가 깨지므로
그대로 따른다.

⚠️ **test는 학습에 쓰지 않는다.** 데이터셋 배포 조건이 "strictly for reporting of
results alone"이다. 이 스크립트는 기본적으로 test를 변환하지 않는다.

⚠️ **인용 의무가 있다.** 결과를 문서화할 때 다음을 인용한다.
    Fallen People Detection Capabilities Using Assistive Robot.
    S. Maldonado-Bascón et al. Electronics 2019.

사용 예:
    python scripts/convert_efpds.py --src "C:/Users/SSAFY/Downloads/E-FPDS/raw" \\
        --out data/efpds

    # 성능 보고 단계에서만
    python scripts/convert_efpds.py --src ... --out data/efpds --include-test
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PERSON_CLASS_ID = 0
# 저자 split 이름 -> YOLO split. test는 기본 제외(배포 조건).
SPLIT_MAP = {"train": "train", "valid": "val", "test": "test"}


def convert_label(text: str, width: int, height: int) -> tuple[list[str], Counter]:
    """`class left right top bot`(절대 픽셀) → YOLO `0 xc yc w h`(정규화)."""
    lines: list[str] = []
    stats: Counter = Counter()
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split()
        if len(parts) != 5:
            stats["형식오류"] += 1
            continue
        try:
            cls = int(float(parts[0]))
            left, right, top, bot = (float(v) for v in parts[1:])
        except ValueError:
            stats["숫자오류"] += 1
            continue

        # 순서가 뒤집힌 줄이 있을 수 있으니 정렬해 둔다.
        if left > right:
            left, right = right, left
        if top > bot:
            top, bot = bot, top
        left, top = max(0.0, left), max(0.0, top)
        right, bot = min(float(width), right), min(float(height), bot)

        bw, bh = right - left, bot - top
        if bw <= 1.0 or bh <= 1.0:
            stats["박스무효"] += 1
            continue

        stats[f"class{cls}"] += 1
        lines.append(
            f"{PERSON_CLASS_ID} "
            f"{(left + right) / 2 / width:.6f} {(top + bot) / 2 / height:.6f} "
            f"{bw / width:.6f} {bh / height:.6f}"
        )
    return lines, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, type=Path,
                    help="train/valid/test 폴더가 있는 곳")
    ap.add_argument("--out", type=Path, default=Path("data/efpds"))
    ap.add_argument("--include-test", action="store_true",
                    help="배포 조건상 결과 보고용으로만 쓴다. 학습에 넣지 않는다")
    args = ap.parse_args()

    from PIL import Image

    splits = dict(SPLIT_MAP)
    if not args.include_test:
        splits.pop("test")

    total = Counter()
    written = Counter()
    skipped = Counter()

    for src_name, dst_name in splits.items():
        src_dir = args.src / src_name
        if not src_dir.is_dir():
            print(f"없음, 건너뜀: {src_dir}", file=sys.stderr)
            continue
        img_out = args.out / "images" / dst_name
        lbl_out = args.out / "labels" / dst_name
        img_out.mkdir(parents=True, exist_ok=True)
        lbl_out.mkdir(parents=True, exist_ok=True)

        for png in sorted(src_dir.rglob("*.png")):
            txt = png.with_suffix(".txt")
            if not txt.exists():
                skipped["라벨없음"] += 1
                continue
            try:
                with Image.open(png) as im:
                    w, h = im.size
            except Exception:  # noqa: BLE001
                skipped["이미지오류"] += 1
                continue

            lines, stats = convert_label(txt.read_text(encoding="utf-8", errors="ignore"), w, h)
            total.update(stats)
            if not lines:
                # 사람이 하나도 없는 이미지는 배경으로 학습된다. 원본 라벨이
                # 비어 있으면 의도된 음성 샘플일 수 있으나, 파싱 실패와
                # 구분되지 않으므로 버린다(값을 지어내지 않는다).
                skipped["박스없음"] += 1
                continue

            # split 이름을 파일명에 남겨 어느 장소인지 추적할 수 있게 한다.
            stem = f"{png.parent.name}_{png.stem}" if png.parent.name != src_name else png.stem
            (img_out / f"{stem}.png").write_bytes(png.read_bytes())
            (lbl_out / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            written[dst_name] += 1

    print("변환 완료")
    for k, v in written.items():
        print(f"  {k:6s} {v}장")
    print(f"\n원본 클래스 분포: "
          f"쓰러짐(1) {total.get('class1', 0)} / "
          f"비쓰러짐(-1) {total.get('class-1', 0)} / "
          f"기타(0) {total.get('class0', 0)}")
    print("  → person 단일 클래스(0)로 합쳤다. 자세 판정은 규칙이 한다")
    bad = {k: v for k, v in total.items() if not k.startswith("class")}
    if bad:
        print(f"  제외된 줄: {bad}")
    if skipped:
        print(f"  건너뛴 이미지: {dict(skipped)}")
    print(f"\n다음: python scripts/validate_yolo_dataset.py --root {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

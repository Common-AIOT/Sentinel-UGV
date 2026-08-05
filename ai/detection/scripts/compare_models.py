"""여러 모델을 여러 데이터셋에서 한 표로 비교한다.

## 왜 필요한가

파인튜닝 판정을 세 번 잘못했다.

1. 같은 도메인 val만 봤다 → mAP50 0.993인데 다른 도메인에서 0%였다
2. `conf=0.50` 고정으로 쟀다 → confidence 눈금이 바뀐 것을 성능 붕괴로 오독했다
3. 프레임당 재현율로 쟀다 → 여러 프레임을 보는 실제 운영과 다른 지표였다

1과 2는 이 스크립트가 막는다. **여러 도메인 × mAP(임계값 무관)** 로 잰다.
3은 정지 이미지 데이터셋으로는 잴 수 없다. 영상 기반 평가가 따로 필요하다
(scripts/check_regression.py, 다만 그것도 프레임 단위라 한계가 있다).

## 교차 평가가 핵심이다

모델이 학습한 데이터셋과 **다른** 데이터셋에서의 숫자가 진짜 일반화 성능이다.

    E-FPDS로 학습한 모델  -> 71550에서 평가   (교차)
    71550으로 학습한 모델 -> E-FPDS에서 평가  (교차)

사용 예:
    python scripts/compare_models.py

    # TTA 포함
    python scripts/compare_models.py --tta

    # 특정 모델만
    python scripts/compare_models.py --models models/yolo26n.pt runs/train/x/weights/best.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# (표시 이름, 가중치 경로, 학습에 쓴 데이터)
DEFAULT_MODELS = [
    ("사전학습(현재)", "models/yolo26n.pt", "COCO"),
    ("E-FPDS 전체", "runs/train/efpds-v1/weights/best.pt", "E-FPDS"),
    ("E-FPDS 동결", "runs/train/efpds-frozen/weights/best.pt", "E-FPDS"),
    ("E-FPDS 동결+저LR", "runs/train/efpds-gentle/weights/best.pt", "E-FPDS"),
    ("71550 전체", "runs/train/smoke2/weights/best.pt", "71550"),
]

# (표시 이름, dataset yaml, 이 데이터로 학습한 모델의 태그)
DEFAULT_DATASETS = [
    ("E-FPDS test", "configs/dataset_efpds_test.yaml", "E-FPDS"),
    ("71550 val", "configs/dataset.yaml", "71550"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", nargs="*", default=None,
                    help="지정하면 이 가중치들만 본다")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--tta", action="store_true",
                    help="test-time augmentation. 추론을 여러 번 해 합친다")
    ap.add_argument("--metric-only", action="store_true",
                    help="진행 로그를 줄인다")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from train_detect import resolve_dataset  # noqa: E402
    from ultralytics import YOLO  # noqa: E402

    models = DEFAULT_MODELS
    if args.models:
        models = [(Path(m).parent.parent.name or m, m, "?") for m in args.models]
    models = [(n, p, t) for n, p, t in models if Path(p).exists()]
    if not models:
        print("평가할 가중치가 없다", file=sys.stderr)
        return 1

    datasets = []
    for name, yml, tag in DEFAULT_DATASETS:
        if Path(yml).exists():
            try:
                datasets.append((name, str(resolve_dataset(Path(yml))), tag))
            except FileNotFoundError as exc:
                print(f"건너뜀 — {name}: {exc}", file=sys.stderr)
    if not datasets:
        print("평가할 데이터셋이 없다", file=sys.stderr)
        return 1

    print(f"imgsz={args.imgsz}  TTA={'켬' if args.tta else '끔'}\n")

    for ds_name, ds_path, ds_tag in datasets:
        print(f"=== {ds_name} ===")
        print(f"{'모델':20s} {'mAP50':>8s} {'mAP50-95':>9s} {'재현율':>8s} {'정밀도':>8s}  비고")
        print("-" * 68)
        for m_name, m_path, m_tag in models:
            try:
                r = YOLO(m_path).val(
                    data=ds_path, imgsz=args.imgsz, classes=[0],
                    verbose=False, plots=False, augment=args.tta,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"{m_name:20s} 실패: {type(exc).__name__}")
                continue
            note = "자기 도메인" if m_tag == ds_tag else ("사전학습" if m_tag == "COCO" else "교차")
            print(f"{m_name:20s} {r.box.map50:8.4f} {r.box.map:9.4f} "
                  f"{r.box.mr:8.4f} {r.box.mp:8.4f}  {note}")
        print()

    print("'교차'가 진짜 일반화 성능이다. '자기 도메인'은 외운 것과 구분되지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

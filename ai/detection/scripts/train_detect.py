"""person Detect 파인튜닝. AGENTS.md §13 규칙을 따른다.

Ultralytics API만 쓴다. 학습 루프를 직접 짜지 않는다.

## 무엇을 고치려는 학습인가

사전학습 COCO 모델이 **누운 사람을 놓친다.** 실측(2026-08-04, AI-Hub 71550 전도):

    밝은 실내·가림 없음·전신 노출 조건에서도 재현율 66.2%
    (754 프레임 중 249건은 IoU=0, 즉 아예 못 잡음)

크기·조명 탓이 아니라 자세 자체가 원인이다. 임계값 조정으로 풀 수 없고
학습으로만 풀린다.

## --freeze — 전체 가중치를 다시 쓰지 않는다

목표는 **기존 성능을 유지하면서 쓰러진 사람을 더 잡는 것**이다. 그런데 기본
파인튜닝은 backbone까지 전부 갱신하므로, 좁은 데이터로 돌리면 COCO 118,000장·
80클래스로 배운 일반화가 덮어씌워진다. 우리 데이터가 아무리 다양해도 COCO보다
다양할 수는 없다.

실제로 데이터를 세 번 바꿔가며 전체 학습했고 세 번 다 같은 방식으로 무너졌다
(71850 기준, 서 있는 사람):

    1차 71550 쓰러짐만 34,488장        100% -> 24%
    2차 + 서 있는 사람 25,540장         69.2% -> 30.1%
    3차 E-FPDS 12개 장소                69.2% -> 4.9%

**데이터를 바꿔도 결과가 같으면 데이터가 원인이 아니다.** `--freeze 10`은
backbone을 고정하고 탐지 head만 학습해 이 경로를 원천 차단한다.

## ⚠️ 데이터 치우침 — 이 스크립트의 가장 큰 함정

`convert_71550.py`가 만드는 데이터는 **전부 전도 클립**이라 쓰러지는 중이거나
누운 사람뿐이다. 이것만으로 파인튜닝하면 누운 자세는 좋아지지만
**서 있는 사람 탐지가 나빠질 수 있다**(catastrophic forgetting).

명세는 서 있거나 앉아 있는 요구조자도 구조 대상으로 둔다
(docs/07-AI-탐지-음성.md 25.1, src/persistence.py). 쓰러진 사람을 더 잡으려다
서 있는 요구조자를 놓치면 손해다.

그래서 이 스크립트는 **학습 전후로 서 있는 사람 성능을 반드시 같이 잰다.**
`--baseline-data`에 서 있는 사람이 포함된 검증셋을 주면 학습 전/후를 비교해
출력한다. 주지 않으면 경고를 띄운다. AGENTS.md §22의 "FPS가 올라도 detections가
줄면 기각"과 같은 원칙이다.

사용 예:
    # 1) smoke test — 배선 확인용. 1 epoch만 돈다
    python scripts/train_detect.py --epochs 1 --name smoke

    # 2) 실제 학습
    python scripts/train_detect.py --epochs 100 --name fallen-v1

    # 3) 서 있는 사람 회귀까지 확인 (권장)
    python scripts/train_detect.py --epochs 100 --name fallen-v1 \\
        --baseline-data configs/dataset_standing.yaml

결과는 runs/train/<name>/weights/best.pt 에 저장된다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# AGENTS.md §13 기본 설정. 재현성을 위해 seed를 고정한다.
DEFAULT_IMGSZ = 640
DEFAULT_SEED = 42


def resolve_dataset(yaml_path: Path) -> Path:
    """dataset yaml의 상대 path를 절대경로로 바꾼 사본을 만들어 그 경로를 돌려준다.

    Ultralytics는 yaml의 `path`가 상대경로면 **yaml 위치가 아니라** 전역 설정
    (%APPDATA%/Ultralytics/settings.json 의 datasets_dir) 기준으로 푼다. 그 값은
    사용자의 다른 프로젝트를 가리킬 수 있고, 실제로 그래서 학습이 엉뚱한 경로를
    찾다 실패했다(2026-08-04 smoke test).

    전역 설정을 고치면 다른 프로젝트가 깨지므로 건드리지 않는다. 대신 여기서
    yaml 위치 기준으로 풀어 절대경로 사본을 만든다. 저장소의 yaml은 상대경로로
    두어 다른 사람 PC에서도 그대로 동작한다.
    """
    import yaml

    cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    raw = cfg.get("path")
    if raw is None:
        return yaml_path  # path가 없으면 train/val이 절대경로일 것이다. 그대로 쓴다.

    base = Path(raw)
    if not base.is_absolute():
        base = (yaml_path.parent / base).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"데이터셋 경로가 없다: {base}")
    cfg["path"] = str(base)

    out = yaml_path.parent / f".{yaml_path.stem}.resolved.yaml"
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    return out


def evaluate(model, data: str, imgsz: int, label: str) -> dict[str, float]:
    """검증셋 성능을 잰다. 학습 전후 비교에 쓴다."""
    print(f"\n--- {label} ---", flush=True)
    m = model.val(data=data, imgsz=imgsz, verbose=False)
    out = {
        "mAP50": float(m.box.map50),
        "mAP50-95": float(m.box.map),
        "recall": float(m.box.mr),
        "precision": float(m.box.mp),
    }
    for k, v in out.items():
        print(f"  {k:10s} {v:.4f}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", default="models/yolo26n.pt",
                    help="시작 가중치. 사전학습 COCO 모델에서 이어 학습한다")
    ap.add_argument("--data", default="configs/dataset.yaml")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=DEFAULT_IMGSZ)
    ap.add_argument("--batch", type=int, default=-1,
                    help="-1이면 Ultralytics가 VRAM에 맞춰 자동으로 정한다. "
                         "개발 PC는 RTX 4050 6GB라 수동 지정 시 OOM에 주의한다")
    ap.add_argument("--device", default="0")
    ap.add_argument("--name", default="detect")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--lr0", type=float, default=None,
                    help="초기 학습률. Ultralytics 기본값 0.01은 사실상 처음부터 학습할 때의 "
                         "값이라 파인튜닝에는 크다. 기존 가중치를 조금만 움직이려면 "
                         "10~100배 낮춘다(예: 0.0005)")
    ap.add_argument("--warmup", type=float, default=None,
                    help="warmup epoch 수. 기본 3.0은 10 epoch 학습에서 30%%를 차지하며 "
                         "그 구간에 가중치가 크게 흔들린다. 0이면 warmup 없이 시작한다")
    ap.add_argument("--freeze", type=int, default=None,
                    help="앞에서부터 이 수만큼의 layer를 얼린다. 10이면 backbone 전체다. "
                         "특징 추출기를 고정하고 탐지 head만 학습하므로 "
                         "사전학습이 배운 일반화가 보존된다(아래 설명 참고)")
    ap.add_argument("--resume", action="store_true",
                    help="중단된 학습을 이어서 한다")
    ap.add_argument("--baseline-data", default=None,
                    help="서 있는 사람이 포함된 검증셋. 학습 전후를 비교해 "
                         "회귀를 잡는다. 위 모듈 설명의 경고 참고")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"데이터셋 정의가 없다: {data_path}", file=sys.stderr)
        return 1

    try:
        data_path = resolve_dataset(data_path)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"데이터셋: {data_path}")

    from ultralytics import YOLO  # 무거우므로 인자 검사 뒤에 부른다

    model = YOLO(args.model)

    baseline = None
    before = None
    if args.baseline_data:
        if not Path(args.baseline_data).exists():
            print(f"기준 검증셋이 없다: {args.baseline_data}", file=sys.stderr)
            return 1
        baseline = str(resolve_dataset(Path(args.baseline_data)))
        before = evaluate(model, baseline, args.imgsz,
                          "학습 전 — 서 있는 사람 기준셋")
    else:
        print(
            "⚠️  --baseline-data 가 없다. 이 학습 데이터는 누운 자세에 치우쳐 있어\n"
            "    서 있는 사람 탐지가 나빠져도 알 수 없다. 명세는 서 있는 요구조자도\n"
            "    구조 대상으로 둔다. 결과를 '검증됨'으로 보고하지 않는다.\n",
            file=sys.stderr,
        )

    model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        seed=args.seed,
        optimizer="auto",
        plots=True,
        freeze=args.freeze,
        # None이면 Ultralytics 기본값을 그대로 쓴다.
        **({"lr0": args.lr0} if args.lr0 is not None else {}),
        **({"warmup_epochs": args.warmup} if args.warmup is not None else {}),
        # project도 절대경로로 준다. 상대경로면 Ultralytics의 기본 runs 디렉터리
        # 아래에 다시 붙어 runs/detect/runs/train/... 처럼 중첩된다.
        project=str(Path("runs/train").resolve()),
        name=args.name,
        resume=args.resume,
        exist_ok=True,
    )

    weights = Path("runs/train").resolve() / args.name / "weights" / "best.pt"
    print(f"\n가중치: {weights}")

    if before is not None:
        after = evaluate(YOLO(str(weights)), baseline, args.imgsz,
                         "학습 후 — 서 있는 사람 기준셋")
        print("\n=== 서 있는 사람 회귀 확인 ===")
        print(f"{'지표':10s} {'학습 전':>9s} {'학습 후':>9s} {'변화':>9s}")
        regressed = []
        for k in before:
            d = after[k] - before[k]
            mark = ""
            # 재현율이 떨어지는 것은 사람을 놓치는 것이라 가장 위험하다(§23).
            if k == "recall" and d < -0.01:
                mark = "  <<< 사람을 더 놓친다"
                regressed.append(k)
            elif d < -0.02:
                mark = "  <<<"
                regressed.append(k)
            print(f"{k:10s} {before[k]:9.4f} {after[k]:9.4f} {d:+9.4f}{mark}")
        if regressed:
            print("\n서 있는 사람 성능이 떨어졌다. 그대로 채택하지 않는다.")
            print("서 있는 사람 데이터를 섞은 뒤 다시 학습한다(configs/dataset.yaml 주석).")
            return 2

    print("\n다음: python -m src.main --model <weights> --source <검증영상> --frame-log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

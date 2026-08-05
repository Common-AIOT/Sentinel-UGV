"""파인튜닝 회귀 검사 — 서 있는 사람을 잃지 않았는지 본다.

## 왜 필요한가

학습 데이터가 쓰러진 사람에 치우쳐 있어 파인튜닝이 **서 있는 사람을 잊을 수 있다**
(catastrophic forgetting). 2026-08-04 1차 학습에서 실제로 그랬다.

    같은 데이터셋 val:  mAP50 0.993, 재현율 97.7%   ← 좋아 보였다
    다른 도메인(71850): 서 있는 사람 100% -> 24%, 99% -> 0%
                        쓰러진 사람마저 100% -> 0%

**같은 도메인 val로는 이 붕괴를 못 잡는다.** 그 장면을 외운 것과 구분되지 않기 때문이다.
그래서 이 스크립트는 학습에 쓰지 않은 **다른 도메인**(71850 실외 CCTV)으로 잰다.

## 판정 기준

명세는 서 있거나 앉아 있는 요구조자도 구조 대상으로 둔다(07-AI-탐지.md 25.1).
쓰러진 사람을 더 잡으려다 서 있는 사람을 놓치면 손해다(AGENTS.md §23: false negative
최우선). 따라서:

    서 있는 사람 5%p 이상 하락  -> 기각 (exit 2)
    누운 사람 상승 + 서 있는 사람 유지 -> 채택

사용 예:
    python scripts/check_regression.py --model runs/train/smoke/weights/best.pt
    python scripts/check_regression.py --model A.pt --baseline models/yolo26n.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 71850 Validation 쓰러짐 클립. (fall_start, fall_end, 총프레임)
# 라벨의 event_frame에서 읽은 값이다.
CLIPS = {
    "E02_041": (1840, 2323, 3600),
    "E02_042": (1778, 3660, 3660),
    "E02_043": (1827, 1996, 3600),
    "E02_045": (1995, 5731, 5732),
}
FAIL_DROP_PP = 5.0  # 서 있는 사람이 이만큼(%p) 떨어지면 기각


def measure(model, cap, lo: int, hi: int, stride: int, conf: float) -> tuple[int, int]:
    """(샘플 프레임 수, 사람이 잡힌 프레임 수)"""
    import cv2

    n = hit = 0
    for f in range(lo, hi, stride):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            continue
        n += 1
        if len(model.predict(img, classes=[0], conf=conf, verbose=False)[0].boxes):
            hit += 1
    return n, hit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True, help="검사할 파인튜닝 가중치")
    ap.add_argument("--baseline", default="models/yolo26n.pt")
    ap.add_argument("--video-dir", type=Path, default=Path("data/pose_test"),
                    help="71850 쓰러짐 클립이 있는 곳")
    ap.add_argument("--stride", type=int, default=15,
                    help="전수는 느리다. 균등 샘플링으로 비율만 본다")
    ap.add_argument("--conf", type=float, default=0.50,
                    help="configs/pipeline.yaml의 detector.confidence와 맞춘다")
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO

    missing = [c for c in CLIPS if not (args.video_dir / f"{c}.mp4").exists()]
    if len(missing) == len(CLIPS):
        print(f"검증 영상이 없다: {args.video_dir}", file=sys.stderr)
        print("71850 Validation 쓰러짐 클립을 배치한다.", file=sys.stderr)
        return 1

    models = {"기준": YOLO(args.baseline), "검사": YOLO(args.model)}
    rows: dict[tuple[str, str], dict[str, float]] = {}

    print(f"{'클립':9s} {'구간':10s} {'기준':>8s} {'검사':>8s} {'변화':>9s}")
    print("-" * 50)
    for clip, (fs, fe, total) in CLIPS.items():
        vp = args.video_dir / f"{clip}.mp4"
        if not vp.exists():
            continue
        for seg, (lo, hi) in (("서있음(전)", (0, fs)),
                              ("쓰러짐(중)", (fs, min(fe, total)))):
            vals = {}
            for name, model in models.items():
                cap = cv2.VideoCapture(str(vp))
                n, hit = measure(model, cap, lo, hi, args.stride, args.conf)
                cap.release()
                if n:
                    vals[name] = hit / n * 100
            if len(vals) == 2:
                d = vals["검사"] - vals["기준"]
                rows[(clip, seg)] = vals
                print(f"{clip:9s} {seg:10s} {vals['기준']:7.1f}% "
                      f"{vals['검사']:7.1f}% {d:+8.1f}p")

    standing = [(k, v) for k, v in rows.items() if k[1].startswith("서있음")]
    fallen = [(k, v) for k, v in rows.items() if k[1].startswith("쓰러짐")]

    def avg(items, key):
        vals = [v[key] for _, v in items]
        return sum(vals) / len(vals) if vals else 0.0

    print("\n=== 요약 (클립 평균) ===")
    s_before, s_after = avg(standing, "기준"), avg(standing, "검사")
    f_before, f_after = avg(fallen, "기준"), avg(fallen, "검사")
    print(f"서 있는 사람  {s_before:.1f}% -> {s_after:.1f}%  ({s_after - s_before:+.1f}p)")
    print(f"쓰러진 사람   {f_before:.1f}% -> {f_after:.1f}%  ({f_after - f_before:+.1f}p)")

    drop = s_before - s_after
    print()
    if drop >= FAIL_DROP_PP:
        print(f"기각. 서 있는 사람이 {drop:.1f}%p 떨어졌다(기준 {FAIL_DROP_PP}%p).")
        print("명세는 서 있는 요구조자도 구조 대상으로 둔다. 그대로 채택하지 않는다.")
        return 2
    if f_after <= f_before:
        print("보류. 서 있는 사람은 지켰으나 쓰러진 사람이 나아지지 않았다.")
        print("학습의 목적을 달성하지 못했다.")
        return 3
    print("채택 가능. 서 있는 사람을 지키면서 쓰러진 사람이 개선됐다.")
    print("⚠️ 도메인 2개(71550 학습 / 71850 검증)로만 확인한 결과다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

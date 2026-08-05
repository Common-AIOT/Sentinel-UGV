"""자세 판정 임계값을 정답 라벨로 보정한다.

## 왜 이제야 가능한가

`posture_classifier.py`의 임계값 7개는 **실측 근거 없이 정해진 값**이다. 실제 쓰러진
사람의 정답 라벨이 없어 맞는지 확인할 방법이 없었다.

**E-FPDS가 그 라벨을 준다.**

    class = 1   쓰러짐        5,023개
    class = -1  비쓰러짐      2,275개   (서 있음·앉음·소파에 누움·걷기)

박스와 정답이 같이 있으므로, 그 박스를 잘라 Pose를 돌리고 우리 규칙을 적용하면
**FALLEN 판정의 정밀도·재현율이 나온다.**

⚠️ 학습이 아니다. 규칙의 임계값만 정답에 맞춘다(AGENTS.md §10 "학습 모델이 아니라
명시적인 규칙").

## 한계 — 먼저 밝힌다

- **정지 이미지라 부동(inactivity) 신호를 쓸 수 없다.** 실제 파이프라인은 4신호,
  여기서는 3신호로 판정한다. 따라서 이 숫자가 운영 성능과 완전히 같지는 않다.
- E-FPDS는 실내 8개 장소다. 우리 배치 환경(복도)과 가깝지만 동일하지 않다.
- `-1`(비쓰러짐)에 **소파·침대에 누운 사람**이 포함된다. 형상만 보면 쓰러짐과
  같으므로 우리 규칙이 틀리기 쉬운 구간이며, 그래서 오히려 좋은 시험이다.

사용 예:
    # 현재 설정의 성능부터 잰다
    python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid

    # 임계값 후보를 훑는다
    python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid --sweep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.posture_classifier import PostureClassifier  # noqa: E402
from src.schemas import POSTURE_FALLEN, Detection, PoseResult  # noqa: E402

FALL, NON_FALL = 1, -1


def load_annotations(split_dir: Path) -> list[tuple[Path, list[tuple[int, tuple[float, ...]]]]]:
    """(이미지 경로, [(클래스, (l, r, t, b)), ...]) 목록.

    E-FPDS 라벨은 `class left right top bot` 절대픽셀이다(x 둘, y 둘).
    """
    out = []
    for png in sorted(split_dir.rglob("*.png")):
        txt = png.with_suffix(".txt")
        if not txt.exists():
            continue
        boxes = []
        for line in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            try:
                cls = int(float(parts[0]))
                left, right, top, bot = (float(v) for v in parts[1:])
            except ValueError:
                continue
            if cls not in (FALL, NON_FALL):
                continue
            if left > right:
                left, right = right, left
            if top > bot:
                top, bot = bot, top
            if right - left <= 1 or bot - top <= 1:
                continue
            boxes.append((cls, (left, top, right, bot)))
        if boxes:
            out.append((png, boxes))
    return out


def evaluate(samples, clf: PostureClassifier, pose_model, keypoint_conf: float,
             crop_margin: float = 0.10):
    """정답 대비 혼동행렬을 만든다."""
    import cv2

    tp = fp = tn = fn = 0
    scores_fall: list[float] = []
    scores_non: list[float] = []

    for png, boxes in samples:
        img = cv2.imread(str(png))
        if img is None:
            continue
        h, w = img.shape[:2]
        for cls, (x1, y1, x2, y2) in boxes:
            det = Detection(class_id=0, class_name="person", confidence=1.0,
                            bbox_xyxy=(x1, y1, x2, y2), track_id=1)

            pose = None
            if pose_model is not None:
                mx = (x2 - x1) * crop_margin
                my = (y2 - y1) * crop_margin
                cx1, cy1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
                cx2, cy2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
                crop = img[cy1:cy2, cx1:cx2]
                if crop.size:
                    r = pose_model.predict(crop, verbose=False)[0]
                    if r.keypoints is not None and len(r.keypoints.xy):
                        xy = r.keypoints.xy[0].tolist()
                        cf = (r.keypoints.conf[0].tolist()
                              if r.keypoints.conf is not None else [1.0] * len(xy))
                        # crop 좌표를 원본으로 되돌린다
                        pose = PoseResult(
                            keypoints_xy=[(px + cx1, py + cy1) for px, py in xy],
                            keypoints_conf=cf,
                        )

            # 정지 이미지라 부동 신호는 쓸 수 없다(모듈 설명의 한계 참고).
            res = clf.classify(det, pose, inactivity=None)
            pred_fallen = res.status == POSTURE_FALLEN
            if cls == FALL:
                scores_fall.append(res.fallen_score)
                tp += pred_fallen
                fn += not pred_fallen
            else:
                scores_non.append(res.fallen_score)
                fp += pred_fallen
                tn += not pred_fallen
    return tp, fp, tn, fn, scores_fall, scores_non


def report(tp, fp, tn, fn, label=""):
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    acc = (tp + tn) / max(tp + fp + tn + fn, 1)
    if label:
        print(f"{label:>10s}", end="  ")
    print(f"정밀도 {prec:.3f}  재현율 {rec:.3f}  F1 {f1:.3f}  정확도 {acc:.3f}"
          f"   (TP {tp} FP {fp} TN {tn} FN {fn})")
    return prec, rec, f1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, type=Path, help="E-FPDS raw 폴더")
    ap.add_argument("--split", default="valid", choices=["train", "valid", "test"],
                    help="test는 배포 조건상 최종 보고에만 쓴다")
    ap.add_argument("--config", type=Path, default=Path("configs/pipeline.yaml"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-pose", action="store_true",
                    help="Pose 없이 형상 신호만으로 평가한다(관절 기여도 확인용)")
    ap.add_argument("--sweep", action="store_true",
                    help="fallen_threshold 후보를 훑는다")
    args = ap.parse_args()

    import yaml

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    p = cfg["posture"]

    split_dir = args.src / args.split
    if not split_dir.is_dir():
        print(f"없음: {split_dir}", file=sys.stderr)
        return 1
    samples = load_annotations(split_dir)
    if args.limit:
        samples = samples[: args.limit]
    n_box = sum(len(b) for _, b in samples)
    n_fall = sum(1 for _, bs in samples for c, _ in bs if c == FALL)
    print(f"{args.split}: 이미지 {len(samples)}장, 박스 {n_box}개 "
          f"(쓰러짐 {n_fall} / 비쓰러짐 {n_box - n_fall})\n")

    pose_model = None
    if not args.no_pose:
        from ultralytics import YOLO
        pose_model = YOLO(cfg["pose"]["model"])

    def make_clf(threshold: float) -> PostureClassifier:
        return PostureClassifier(
            torso_horizontal_deg=p["torso_horizontal_deg"],
            bbox_aspect_ratio=p["bbox_aspect_ratio"],
            vertical_extent_ratio=p["vertical_extent_ratio"],
            upright_angle_deg=p.get("upright_angle_deg", 30.0),
            min_valid_keypoints=p["min_valid_keypoints"],
            keypoint_confidence=cfg["pose"]["keypoint_confidence"],
            depth_tilt=p.get("depth_tilt", True),
            torso_shoulder_ratio=p.get("torso_shoulder_ratio", 1.3),
            fallen_threshold=threshold,
            weight_torso_angle=p.get("weight_torso_angle", 1.0),
            weight_vertical_extent=p.get("weight_vertical_extent", 1.0),
            weight_bbox_aspect=p.get("weight_bbox_aspect", 0.8),
            inactivity_boost=p.get("inactivity_boost", 0.4),
            width_torso_angle=p.get("width_torso_angle", 12.0),
            width_vertical_extent=p.get("width_vertical_extent", 0.08),
            width_bbox_aspect=p.get("width_bbox_aspect", 0.25),
        )

    base_th = p.get("fallen_threshold", 0.5)
    tp, fp, tn, fn, sf, sn = evaluate(
        samples, make_clf(base_th), pose_model, cfg["pose"]["keypoint_confidence"])

    print(f"=== 현재 설정 (fallen_threshold={base_th}) ===")
    report(tp, fp, tn, fn)

    if sf and sn:
        def med(v):
            return sorted(v)[len(v) // 2]
        print(f"\n점수 분포  쓰러짐 중앙 {med(sf):.3f} / 비쓰러짐 중앙 {med(sn):.3f}")
        if med(sf) <= med(sn):
            print("⚠️ 쓰러짐 점수가 비쓰러짐보다 높지 않다. 임계값 조정으로 풀리지 않는다.")

    if args.sweep and sf and sn:
        print("\n=== fallen_threshold 후보 ===")
        best = (0.0, None)
        for th in [round(0.05 * i, 2) for i in range(1, 20)]:
            t2 = sum(1 for s in sf if s >= th)
            f2 = sum(1 for s in sn if s >= th)
            prec, rec, f1 = report(t2, f2, len(sn) - f2, len(sf) - t2, f"{th:.2f}")
            if f1 > best[0]:
                best = (f1, th)
        if best[1] is not None:
            print(f"\nF1 최대: threshold {best[1]} (F1 {best[0]:.3f})")
            print("⚠️ F1만 보고 정하지 않는다. 미탐이 최우선 리스크이므로"
                  "(AGENTS.md §23) 재현율을 우선한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

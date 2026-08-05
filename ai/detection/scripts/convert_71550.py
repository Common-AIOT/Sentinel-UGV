"""AI-Hub 71550(실내 편의점·매장 사람 이상행동) 전도 → YOLO 학습 데이터 변환.

## 왜 관절에서 bbox를 만드는가

이 데이터셋의 전도 클래스에는 **사람 bounding box 라벨이 없다.** box는 이벤트 구간을
표시하는 `fall_start` / `fall_end`뿐이다(실측 2026-08-04: box 4개 중 4개가 이벤트 표시).

대신 사람이 라벨링한 **17개 관절 좌표**가 있으므로, 그것을 모두 감싸는 사각형을 만든다.
추정이 아니라 정답 좌표에서 나온 기하 계산이라 오차가 없다. SAM 같은 추가 모델을 쓰면
정답이 있는 자리에 추정을 끼워 넣게 되고 검수 기준도 사라진다.

관절은 몸의 끝이 아니므로(정수리·손끝·발끝·옷이 바깥에 있다) `--margin`으로 여유를 준다.
기본값 0.12는 실측에서 YOLO 탐지와의 IoU 중앙값 0.721을 준 값이며, `visualize_labels.py`로
눈으로 확인한 뒤 조정한다.

## 관절 체계가 COCO가 아니다

Azure Kinect 계열 17관절(Pelvis / Spine naval / Spine chest / Neck base / Center head 등)이라
COCO 17(코·눈·귀·손목·발목)과 다르다. **Detect 학습에는 문제가 없다**(bbox만 쓰므로).
Pose 모델 파인튜닝에는 그대로 쓸 수 없다.

## split은 반드시 scene 단위다

같은 쓰러짐 장면을 카메라 4대(CA/CB/CC/CD)가 동시에 찍는다. 클립 단위로 나누면
**같은 장면이 train과 val에 모두 들어가 누수가 발생**하고, 검증 성능이 실제보다 높게 나온다.
파일명에서 카메라 코드와 초를 떼어 scene 키를 만든다(AGENTS.md §12 "가능하면 group/scene 단위 split").

## --standing — 서 있는 사람을 함께 넣는다 (필수)

기본 모드는 **쓰러지는 구간만** 뽑는다. 그것만으로 학습하면 모델이 무너진다.
2026-08-04 실측(1 epoch, 34,488장)에서 도메인이 다른 71850으로 재보니:

    서 있는 사람 탐지  100% -> 24%,  99% -> 0%,  68% -> 0%
    쓰러진 사람마저    100% -> 0%

같은 데이터셋 val에서는 mAP50 0.993이 나왔다. **그 장면을 외웠을 뿐이다.**
원인은 학습 데이터에 서 있는 자세가 하나도 없다는 것이다.

`--standing`은 같은 클립의 `fall_start` **이전** 구간을 뽑는다. 관절 라벨이 없는
구간이므로 사전학습 모델로 라벨을 만든다.

**같은 클립을 쓰는 것이 핵심이다.** 실외 데이터를 섞으면 모델이
"실내=쓰러짐, 실외=서있음"이라는 지름길을 배울 수 있다. 같은 매장·같은 카메라에서
자세만 다른 쌍을 주면 그 지름길이 막힌다.

⚠️ 사전학습 모델 출력을 라벨로 쓰는 것(pseudo-labeling)은 **모르는 것을 가르치는
데는 쓸 수 없다.** 여기서는 반대로, 이미 99~100% 잡는 서 있는 사람을 **잊지 말라고
붙잡아두는 용도**다. 그래도 정답은 아니므로 "검증됨"으로 보고하지 않는다.

사용 예:
    # 1) 쓰러진 사람 (관절에서 유도한 정답 bbox)
    python scripts/convert_71550.py \\
        --xml-dir  "D:/71550/Training/02.라벨링데이터/TL_03.이상행동_07.전도" \\
        --video-dir "D:/71550/Training/01.원천데이터/TS_03.이상행동_07.전도" \\
        --out data/processed

    # 2) 서 있는 사람 (같은 out에 덧붙인다. scene 분할이 동일해야 하므로
    #    --seed 와 --val-ratio 를 1)과 같게 둔다)
    python scripts/convert_71550.py --xml-dir ... --video-dir ... \\
        --out data/processed --standing

    # 먼저 몇 개만 돌려 확인한다
    python scripts/convert_71550.py --xml-dir ... --video-dir ... --limit 5 --dry-run

출력은 AGENTS.md §12의 configs/dataset.yaml 배치를 따른다.
    data/processed/images/{train,val}/*.jpg
    data/processed/labels/{train,val}/*.txt
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# Azure Kinect 계열 17관절. 이벤트 표시 라벨(fall_start 등)과 구분하는 데 쓴다.
KEYPOINT_LABELS = frozenset(
    {
        "Pelvis", "Left hip", "Left knee", "Left foot",
        "Right  hip", "Right knee", "Right foot",  # 원본에 공백 2개다. 고치지 않는다.
        "Spine naval", "Spine chest", "Neck base", "Center head",
        "Right shoulder", "Right elbow", "Right hand",
        "Left shoulder", "Left elbow", "Left hand",
    }
)

PERSON_CLASS_ID = 0

# C_3_7_10_BU_DYA_08-23_13-47-37_CA_RGB_DF2_M2
#                             ^^^^^^^^ ^^ 초와 카메라 코드를 떼어 scene 키를 만든다
_CAMERA_TOKEN = re.compile(r"^C[A-Z]$")


def scene_key(stem: str) -> str:
    """같은 장면을 여러 카메라가 찍은 클립을 하나로 묶는 키.

    카메라 코드(CA/CB/CC/CD)를 빼고, 시각에서 초를 떼어 분 단위로 맞춘다.
    카메라마다 녹화 시작이 몇 초 어긋나기 때문이다(실측: 13-47-37 / 13-47-40).

    같은 분·같은 장소의 서로 다른 장면이 합쳐질 수는 있으나, 그 방향의 오차는
    train/val을 더 엄격히 가르는 쪽이라 누수를 만들지 않는다.
    """
    parts = []
    for tok in stem.split("_"):
        if _CAMERA_TOKEN.match(tok):
            continue
        # HH-MM-SS → HH-MM
        if re.match(r"^\d{2}-\d{2}-\d{2}$", tok):
            tok = tok.rsplit("-", 1)[0]
        parts.append(tok)
    return "_".join(parts)


def parse_frames(
    xml_path: Path,
) -> tuple[dict[int, list[tuple[float, float]]], int, int, int | None]:
    """프레임별 관절 좌표, 원본 해상도, fall_start 프레임을 읽는다.

    outside="1"은 트랙이 끝났음을 알리는 표시라 실제 관측이 아니다. 제외한다.
    """
    root = ET.parse(xml_path).getroot()
    size = root.find(".//original_size")
    width = int(size.find("width").text)
    height = int(size.find("height").text)

    frames: dict[int, list[tuple[float, float]]] = defaultdict(list)
    fall_start: int | None = None
    for track in root.findall("track"):
        label = track.get("label")
        if label in KEYPOINT_LABELS:
            for pt in track.findall("points"):
                if pt.get("outside") != "0":
                    continue
                x_str, y_str = pt.get("points").split(",")
                frames[int(pt.get("frame"))].append((float(x_str), float(y_str)))
        elif label == "fall_start":
            for box in track.findall("box"):
                if box.get("outside") == "0":
                    f = int(box.get("frame"))
                    fall_start = f if fall_start is None else min(fall_start, f)
    return frames, width, height, fall_start


def bbox_from_keypoints(
    points: list[tuple[float, float]], width: int, height: int, margin: float
) -> tuple[float, float, float, float] | None:
    """관절을 모두 감싸는 bbox를 만들고 여유를 준 뒤 화면 안으로 자른다.

    반환은 정규화된 YOLO 형식 (x_center, y_center, w, h)이다.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)

    mx = (x2 - x1) * margin
    my = (y2 - y1) * margin
    x1, y1 = max(0.0, x1 - mx), max(0.0, y1 - my)
    x2, y2 = min(float(width), x2 + mx), min(float(height), y2 + my)

    bw, bh = x2 - x1, y2 - y1
    if bw <= 1.0 or bh <= 1.0:
        # 관절이 한 점에 몰린 이상치. 지어내지 않고 버린다.
        return None
    return (
        (x1 + x2) / 2.0 / width,
        (y1 + y2) / 2.0 / height,
        bw / width,
        bh / height,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xml-dir", required=True, type=Path, help="TL_..._전도 (라벨 XML)")
    ap.add_argument("--video-dir", required=True, type=Path, help="TS_..._전도 (원천 mp4)")
    ap.add_argument("--out", type=Path, default=Path("data/processed"))
    ap.add_argument("--margin", type=float, default=0.12,
                    help="관절 바깥 여유 비율. 관절은 몸의 끝이 아니다")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=0, help="클립 수 제한(확인용)")
    ap.add_argument("--min-keypoints", type=int, default=4,
                    help="이 수 미만이면 bbox가 몸 일부만 감싸므로 버린다")
    ap.add_argument("--dry-run", action="store_true",
                    help="영상 디코딩 없이 통계만 낸다")
    ap.add_argument("--resume", action="store_true",
                    help="이미 만들어진 이미지·라벨 짝은 건너뛴다. 중단 후 이어서 돌릴 때 쓴다")
    ap.add_argument("--standing", action="store_true",
                    help="쓰러지기 **전** 구간을 뽑는다. 자세한 설명은 아래 STANDING 참고")
    ap.add_argument("--standing-stride", type=int, default=3,
                    help="--standing에서 몇 프레임마다 뽑을지. 3이면 약 3만 장이 된다")
    ap.add_argument("--label-model", default="models/yolo26n.pt",
                    help="--standing의 라벨을 만들 사전학습 모델. 파인튜닝 가중치를 쓰지 않는다")
    ap.add_argument("--label-conf", type=float, default=0.40)
    args = ap.parse_args()

    xmls = sorted(args.xml_dir.glob("*.xml"))
    if args.limit:
        xmls = xmls[: args.limit]
    if not xmls:
        print(f"XML을 찾을 수 없다: {args.xml_dir}", file=sys.stderr)
        return 1

    # --- scene 단위로 train/val 분할 ---
    by_scene: dict[str, list[Path]] = defaultdict(list)
    for xp in xmls:
        by_scene[scene_key(xp.stem)].append(xp)
    scenes = sorted(by_scene)
    random.Random(args.seed).shuffle(scenes)
    n_val = max(1, int(len(scenes) * args.val_ratio))
    val_scenes = set(scenes[:n_val])

    print(f"클립 {len(xmls)}개 → scene {len(scenes)}개 "
          f"(카메라 {len(xmls)/max(len(scenes),1):.1f}대/장면)")
    print(f"train scene {len(scenes) - n_val} / val scene {n_val}\n")

    if not args.dry_run:
        import cv2  # 무거우므로 실제 변환할 때만 불러온다

    for split in ("train", "val"):
        (args.out / "images" / split).mkdir(parents=True, exist_ok=True)
        (args.out / "labels" / split).mkdir(parents=True, exist_ok=True)

    stats = {"written": 0, "no_video": 0, "few_kp": 0, "bad_bbox": 0,
             "no_frame": 0, "resumed": 0, "no_person": 0}
    per_split: dict[str, int] = defaultdict(int)

    detector = None
    if args.standing and not args.dry_run:
        from ultralytics import YOLO
        detector = YOLO(args.label_model)

    for clip_no, xp in enumerate(xmls, 1):
        split = "val" if scene_key(xp.stem) in val_scenes else "train"
        frames, width, height, fall_start = parse_frames(xp)

        if args.standing:
            # 쓰러지기 **전** 구간. 관절 라벨이 없으므로 사전학습 모델로 라벨을 만든다.
            if fall_start is None or fall_start < args.standing_stride:
                continue
            frames = {f: [] for f in range(0, fall_start, args.standing_stride)}
        if not frames:
            continue

        video = args.video_dir / f"{xp.stem}.mp4"
        if not video.exists():
            stats["no_video"] += 1
            continue

        # 진행 상황을 남긴다. 34,000장 규모라 중간에 끊을지 판단하려면 필요하다.
        if clip_no % 25 == 0 or clip_no == len(xmls):
            print(f"  [{clip_no}/{len(xmls)}] {stats['written']}장 "
                  f"(건너뜀 {stats['resumed']})", flush=True)

        cap = None
        for frame_idx in sorted(frames):
            boxes: list[tuple[float, float, float, float]] = []
            if not args.standing:
                points = frames[frame_idx]
                if len(points) < args.min_keypoints:
                    stats["few_kp"] += 1
                    continue
                box = bbox_from_keypoints(points, width, height, args.margin)
                if box is None:
                    stats["bad_bbox"] += 1
                    continue
                boxes = [box]

            name = f"{xp.stem}_f{frame_idx:05d}"
            img_out = args.out / "images" / split / f"{name}.jpg"
            lbl_out = args.out / "labels" / split / f"{name}.txt"

            # 짝이 **둘 다** 있을 때만 건너뛴다. 이미지만 쓰고 죽은 프레임은 다시 만든다.
            if args.resume and not args.dry_run and img_out.exists() and lbl_out.exists():
                stats["resumed"] += 1
                per_split[split] += 1
                continue

            if not args.dry_run:
                # 건너뛸 프레임이 많을 수 있으므로 실제로 읽을 때 연다.
                if cap is None:
                    cap = cv2.VideoCapture(str(video))
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, img = cap.read()
                if not ok:
                    stats["no_frame"] += 1
                    continue

                if args.standing:
                    res = detector.predict(
                        img, classes=[0], conf=args.label_conf, verbose=False
                    )[0]
                    for x1, y1, x2, y2 in res.boxes.xyxy.tolist():
                        boxes.append((
                            (x1 + x2) / 2 / width, (y1 + y2) / 2 / height,
                            (x2 - x1) / width, (y2 - y1) / height,
                        ))
                    if not boxes:
                        # 사람이 안 잡힌 프레임은 버린다. 실제로는 있는데 모델이
                        # 놓친 것일 수 있고, 그러면 또 배경으로 가르치게 된다.
                        stats["no_person"] += 1
                        continue

                cv2.imwrite(str(img_out), img)
                # 라벨을 이미지 뒤에 쓴다. 사이에서 끊기면 짝이 안 맞아
                # --resume이 그 프레임을 다시 만든다(둘 다 있을 때만 건너뛴다).
                lbl_out.write_text(
                    "".join(
                        f"{PERSON_CLASS_ID} {b[0]:.6f} {b[1]:.6f} {b[2]:.6f} {b[3]:.6f}\n"
                        for b in boxes
                    ),
                    encoding="utf-8",
                )
            stats["written"] += 1
            per_split[split] += 1

        if cap is not None:
            cap.release()

    print(f"{'(건너뜀 — dry-run)' if args.dry_run else '변환 완료'}")
    print(f"  train {per_split['train']}장 / val {per_split['val']}장 "
          f"= 합계 {stats['written']}장")
    skipped = {k: v for k, v in stats.items() if k != "written" and v}
    if skipped:
        print(f"  제외: {skipped}")
    if not args.dry_run:
        print(f"\n다음: python scripts/validate_yolo_dataset.py --root {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

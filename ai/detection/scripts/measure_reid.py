"""ReID 임베딩이 사람을 실제로 구분하는지 측정한다.

## 왜 이 측정이 먼저인가

trackId 재식별(갤러리 방식)을 구현하려면 **임베딩이 같은 사람과 다른 사람을 가를 수
있어야** 한다. 그게 안 되면 갤러리를 아무리 잘 만들어도 의미가 없다.

나쁜 전례가 있다. `tracker_sentinel.yaml`에서 `model: auto`로 두면 YOLO26 Detect
헤드가 end2end라 `yolo26n-cls.pt`(ImageNet 분류)로 조용히 대체되는데, 그때
**타인 간 유사도가 0.835**로 나와 ID가 뒤바뀌었다. 그래서 `yolo26n-reid.onnx`를
명시했지만 **그 모델의 성능은 아직 아무도 재지 않았다.**

## 어떻게 재나 — 71550 다중 카메라

71550은 **같은 쓰러짐 장면을 카메라 3~4대가 동시에** 찍는다. 파일명에서 카메라
코드를 떼면 같은 장면(=같은 사람)끼리 묶인다.

    같은 사람, 다른 카메라  -> 양성 쌍   (시점이 아예 달라 재등장보다 어려운 조건)
    다른 장면              -> 음성 쌍

여기서 갈리면 재등장(같은 카메라, 시간차)은 더 쉽다.

## 함께 재는 것

1. **자세 변화** — 같은 사람의 서 있을 때 vs 누웠을 때. ReID 모델은 서서 걷는
   보행자로 학습되어 누운 사람에서 가장 부정확할 것으로 예상된다.
2. **회전 정규화** — 누운 crop을 세워서 넣으면 학습 분포에 가까워지는가.
   상체 각도만큼 되돌린다. 가설이며 효과는 미지수다.

## 판정 기준

양성/음성 분포가 겹치면 갤러리 방식은 성립하지 않는다. AUC와 **분리도**
(양성 평균 - 음성 평균)를 본다. 임계값 후보도 함께 출력한다.

사용 예:
    python scripts/measure_reid.py --limit-scenes 40
    python scripts/measure_reid.py --limit-scenes 40 --rotate   # 회전 정규화 비교
"""

from __future__ import annotations

import argparse
import math
import random
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from convert_71550 import KEYPOINT_LABELS, scene_key  # noqa: E402

REID_SIZE = (128, 256)  # (w, h) — ReID 관례. 사람은 세로로 길다


def load_clip(xml_path: Path):
    """(프레임 -> 관절 목록, fall_start, 해상도)"""
    root = ET.parse(xml_path).getroot()
    size = root.find(".//original_size")
    w, h = int(size.find("width").text), int(size.find("height").text)
    frames = defaultdict(list)
    fall_start = None
    for track in root.findall("track"):
        lab = track.get("label")
        if lab in KEYPOINT_LABELS:
            for pt in track.findall("points"):
                if pt.get("outside") == "0":
                    x, y = pt.get("points").split(",")
                    frames[int(pt.get("frame"))].append((float(x), float(y)))
        elif lab == "fall_start":
            for b in track.findall("box"):
                if b.get("outside") == "0":
                    f = int(b.get("frame"))
                    fall_start = f if fall_start is None else min(fall_start, f)
    return frames, fall_start, (w, h)


def crop_person(img, points, margin=0.12):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    h, w = img.shape[:2]
    x1, y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
    x2, y2 = int(min(w, x2 + mx)), int(min(h, y2 + my))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return img[y1:y2, x1:x2]


def torso_angle(points) -> float | None:
    """어깨중점→엉덩이중점이 수직축과 이루는 각도. 회전 정규화에 쓴다.

    convert_71550의 관절 순서를 모르므로 좌표 분포로 근사한다. 관절 전체의
    주축(장축) 방향을 각도로 쓴다. 누우면 장축이 수평에 가까워진다.
    """
    if len(points) < 4:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    # 공분산 행렬의 주축 각도
    return math.degrees(0.5 * math.atan2(2 * sxy, sxx - syy))


def embed(session, crops, rotate_deg=None):
    import cv2
    import numpy as np

    batch = []
    for c in crops:
        if rotate_deg is not None:
            h, w = c.shape[:2]
            m = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, 1.0)
            cos, sin = abs(m[0, 0]), abs(m[0, 1])
            nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
            m[0, 2] += nw / 2 - w / 2
            m[1, 2] += nh / 2 - h / 2
            c = cv2.warpAffine(c, m, (nw, nh))
        r = cv2.resize(c, REID_SIZE)
        r = cv2.cvtColor(r, cv2.COLOR_BGR2RGB).astype("float32") / 255.0
        batch.append(r.transpose(2, 0, 1))
    if not batch:
        return None
    arr = np.stack(batch)
    out = session.run(None, {session.get_inputs()[0].name: arr})[0]
    norm = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norm, 1e-9)


def cosine(a, b):
    return float((a * b).sum())


def stats(name, vals):
    if not vals:
        print(f"{name}: 없음")
        return 0.0
    s = sorted(vals)
    mean = sum(s) / len(s)
    print(f"{name:22s} n={len(s):5d}  평균 {mean:.3f}  "
          f"중앙 {s[len(s)//2]:.3f}  p10 {s[len(s)//10]:.3f}  p90 {s[len(s)*9//10]:.3f}")
    return mean


def auc(pos, neg):
    """양성이 음성보다 높을 확률. 0.5면 무작위."""
    if not pos or not neg:
        return 0.0
    neg_sorted = sorted(neg)
    total = 0
    for p in pos:
        lo, hi = 0, len(neg_sorted)
        while lo < hi:
            mid = (lo + hi) // 2
            if neg_sorted[mid] < p:
                lo = mid + 1
            else:
                hi = mid
        total += lo
    return total / (len(pos) * len(neg))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xml-dir", type=Path, required=True)
    ap.add_argument("--video-dir", type=Path, required=True)
    ap.add_argument("--model", default="models/yolo26n-reid.onnx")
    ap.add_argument("--limit-scenes", type=int, default=40)
    ap.add_argument("--rotate", action="store_true",
                    help="누운 crop을 세워서도 재고 비교한다")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import cv2
    import onnxruntime as ort

    providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                 if p in ort.get_available_providers()]
    sess = ort.InferenceSession(args.model, providers=providers)
    print(f"ReID: {args.model}  ({providers[0]})\n")

    by_scene = defaultdict(list)
    for xp in sorted(args.xml_dir.glob("*.xml")):
        by_scene[scene_key(xp.stem)].append(xp)
    scenes = sorted(k for k, v in by_scene.items() if len(v) >= 2)
    random.Random(args.seed).shuffle(scenes)
    scenes = scenes[: args.limit_scenes]
    print(f"장면 {len(scenes)}개 (카메라 2대 이상)")

    # 장면별로 (서 있는 crop, 누운 crop)을 카메라마다 하나씩 모은다
    stand: dict[str, list] = defaultdict(list)
    lie: dict[str, list] = defaultdict(list)
    lie_angle: dict[str, list] = defaultdict(list)

    for sc in scenes:
        for xp in by_scene[sc]:
            vp = args.video_dir / f"{xp.stem}.mp4"
            if not vp.exists():
                continue
            frames, fs, _ = load_clip(xp)
            if not frames or fs is None:
                continue
            cap = cv2.VideoCapture(str(vp))
            # 서 있는 구간: fall_start 이전 (관절 라벨은 없으므로 프레임만)
            # 누운 구간: 관절 라벨이 있는 마지막 프레임
            lie_f = max(frames)
            for tag, fidx, pts in (("lie", lie_f, frames[lie_f]),):
                cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
                ok, img = cap.read()
                if ok:
                    c = crop_person(img, pts)
                    if c is not None:
                        lie[sc].append(c)
                        lie_angle[sc].append(torso_angle(pts))
            # 서 있는 구간은 관절이 없어 첫 라벨 프레임의 박스를 재사용한다
            first_f = min(frames)
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, fs // 2))
            ok, img = cap.read()
            if ok:
                c = crop_person(img, frames[first_f])
                if c is not None:
                    stand[sc].append(c)
            cap.release()

    def pairs(store, label, rotate=None, angles=None):
        pos, neg = [], []
        embs = {}
        for sc, crops in store.items():
            if len(crops) < 2:
                continue
            deg = None
            if rotate and angles and angles.get(sc):
                a = angles[sc][0]
                deg = -a if a is not None else None
            e = embed(sess, crops, rotate_deg=deg)
            if e is not None:
                embs[sc] = e
        keys = sorted(embs)
        for sc in keys:
            e = embs[sc]
            for i in range(len(e)):
                for j in range(i + 1, len(e)):
                    pos.append(cosine(e[i], e[j]))
        rnd = random.Random(args.seed)
        for _ in range(min(3000, len(keys) * 40)):
            a, b = rnd.sample(keys, 2) if len(keys) >= 2 else (None, None)
            if a is None:
                break
            neg.append(cosine(rnd.choice(embs[a]), rnd.choice(embs[b])))
        print(f"\n=== {label} ===")
        pm = stats("같은 사람(다른 카메라)", pos)
        nm = stats("다른 사람", neg)
        print(f"{'분리도':22s} {pm - nm:+.3f}      AUC {auc(pos, neg):.3f}")
        return pos, neg

    lp, ln = pairs(lie, "누운 자세")
    sp, sn = pairs(stand, "서 있는 자세")
    if args.rotate:
        pairs(lie, "누운 자세 + 회전 정규화", rotate=True, angles=lie_angle)

    print("\n판정: AUC 0.5는 무작위. 0.8 이상이면 갤러리 방식이 성립할 여지가 있다.")
    print("      분리도가 0.1 미만이면 임계값을 어디에 둬도 갈리지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

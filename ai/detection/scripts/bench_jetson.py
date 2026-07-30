"""설정 조합 A/B 벤치 러너.

같은 입력 영상에 대해 설정을 하나씩 바꿔가며 돌리고, FPS와 **탐지력 지표를 같은 표에**
출력한다. FPS만 보면 해상도를 낮춰 빠르게 만들고 사람을 놓치는 변경을 통과시키게 된다.
재난 탐색에서 person false negative가 최우선 리스크이므로(AGENTS.md §23) 두 값을
항상 같이 본다.

사용 예:
    # 기본 조합(FP16 유/무, Pose 전역 예산 유/무)
    python scripts/bench_jetson.py --source data/pose_test/walk4.mp4 \\
        --config configs/pipeline.jetson.yaml

    # 해상도까지 비교 (Phase 3)
    python scripts/bench_jetson.py --source data/pose_test/walk4.mp4 \\
        --config configs/pipeline.jetson.yaml --imgsz 640 512 416

    # TensorRT 엔진과 비교 (엔진은 Jetson에서 직접 구워야 한다)
    #   yolo export model=models/yolo26n.pt format=engine half=True device=0
    python scripts/bench_jetson.py --source data/pose_test/walk4.mp4 \\
        --config configs/pipeline.jetson.yaml \\
        --models models/yolo26n.pt models/yolo26n.engine

결과 판정 기준은 AGENTS.md §22를 따른다.
    FPS가 오르고 detections가 유지되면  → 채택
    FPS가 올라도 detections가 줄면      → 기각 (탐지력 손실)
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# scripts/에서 실행해도 src 패키지를 찾을 수 있게 한다.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.main import load_config  # noqa: E402
from src.pipeline import InferencePipeline  # noqa: E402


def build_cases(args: argparse.Namespace, base: dict[str, Any]) -> list[tuple[str, dict]]:
    """(이름, 설정) 목록을 만든다.

    인자로 축을 지정하지 않으면 기본 A/B 두 축(FP16, Pose 전역 예산)을 돌린다.
    두 축 모두 코드 변경 없이 설정만 바꾸는 것이라 Jetson에서 바로 비교할 수 있다.
    """
    cases: list[tuple[str, dict]] = []

    def variant(name: str, mutate) -> None:
        cfg = copy.deepcopy(base)
        mutate(cfg)
        cases.append((name, cfg))

    if args.models:
        for model in args.models:
            variant(f"model={Path(model).name}", lambda c, m=model: c["detector"].update(model=m))

    if args.imgsz:
        for size in args.imgsz:
            variant(f"imgsz={size}", lambda c, s=size: c["detector"].update(imgsz=s))

    if args.fp16:
        variant("fp32", lambda c: c["detector"].pop("quantize", None))
        variant("fp16", lambda c: c["detector"].update(quantize=16))

    if args.pose_budget:
        # 발견 1 검증용. false면 사람 N명일 때 Pose가 초당 2N회가 된다.
        variant("pose_budget=global", lambda c: c["pose_trigger"].update(global_budget=True))
        variant("pose_budget=per_track", lambda c: c["pose_trigger"].update(global_budget=False))

    if not cases:
        cases.append(("baseline", copy.deepcopy(base)))
    return cases


def run_case(
    name: str,
    config: dict[str, Any],
    *,
    source: str,
    device: str | None,
    max_frames: int | None,
    warmup: int,
) -> dict[str, Any]:
    """한 조합을 돌리고 지표를 뽑는다.

    산출물(JSONL·이벤트 이미지)은 임시 디렉터리에 쓰고 지운다. 벤치는 성능 측정이
    목적이고, 조합 수만큼 runs/가 쌓이면 SD카드를 잡아먹는다.
    """
    # 벤치 중에는 프레임 로그를 끈다. 디스크 쓰기가 측정값에 섞인다.
    config = copy.deepcopy(config)
    config.setdefault("output", {})["write_frame_log"] = False

    tmp = Path(tempfile.mkdtemp(prefix="bench_"))
    try:
        pipeline = InferencePipeline(config, output_dir=tmp, device=device)
        stats = pipeline.run_video(source, max_frames=max_frames, warmup_frames=warmup)
        pipeline.close()
        data = stats.to_dict()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {
        "case": name,
        "steady_fps": round(stats.steady_fps, 2),
        "avg_fps": round(stats.avg_fps, 2),
        "frames": stats.frames,
        "detections": stats.detections,
        "person_events": stats.person_events,
        "pose_runs": stats.pose_runs,
        "stage_ms": data.get("stage_ms_per_frame", {}),
        "pose_ms_per_run": data.get("pose_ms_per_run"),
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    header = (
        f"{'case':<26}{'steady':>9}{'avg':>8}{'det':>8}{'events':>8}"
        f"{'pose':>7}{'det ms':>9}{'pose ms':>9}{'post ms':>9}"
    )
    print()
    print(header)
    print("-" * len(header))
    for row in rows:
        stage = row["stage_ms"] or {}
        print(
            f"{row['case']:<26}"
            f"{row['steady_fps']:>9.2f}"
            f"{row['avg_fps']:>8.2f}"
            f"{row['detections']:>8}"
            f"{row['person_events']:>8}"
            f"{row['pose_runs']:>7}"
            f"{stage.get('detect', 0):>9.1f}"
            f"{stage.get('pose', 0):>9.1f}"
            f"{stage.get('post', 0):>9.1f}"
        )

    if len(rows) < 2:
        return

    # 첫 조합을 기준으로 삼아 탐지력 회귀를 표시한다. 사람을 놓치는 변경은
    # FPS가 아무리 올라도 채택하면 안 된다(AGENTS.md §23).
    base = rows[0]
    print()
    print(f"기준: {base['case']}  (detections {base['detections']})")
    for row in rows[1:]:
        if base["detections"] <= 0:
            continue
        fps_delta = (row["steady_fps"] - base["steady_fps"]) / max(base["steady_fps"], 1e-9)
        det_delta = (row["detections"] - base["detections"]) / base["detections"]

        # 탐지력 손실은 FPS 이득과 상쇄되지 않는다. 사람을 놓치면 그것으로 기각이다
        # (AGENTS.md §23). 측정 노이즈를 감안해 1% 이내 변동은 동일로 본다.
        if det_delta < -0.01:
            verdict = "기각 — 탐지력 손실"
        elif fps_delta > 0.01:
            verdict = "채택 후보"
        elif fps_delta < -0.01:
            verdict = "이득 없음 — 기준이 더 빠름"
        else:
            verdict = "차이 없음"

        print(
            f"  {row['case']:<26} FPS {fps_delta:+6.1%}   detections {det_delta:+6.1%}   {verdict}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="기준 입력 영상 경로")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "configs" / "pipeline.jetson.yaml"),
        help="기준 설정 파일",
    )
    parser.add_argument("--device", default=None, help="추론 device (Jetson GPU는 0)")
    parser.add_argument("--max-frames", type=int, default=None, help="처리할 최대 프레임 수")
    parser.add_argument(
        "--warmup",
        type=int,
        default=30,
        help="정상 상태 FPS에서 제외할 앞쪽 프레임 수 (기본 30)",
    )
    parser.add_argument("--models", nargs="*", default=None, help="비교할 Detect 모델 경로들")
    parser.add_argument("--imgsz", nargs="*", type=int, default=None, help="비교할 추론 해상도들")
    parser.add_argument("--fp16", action="store_true", help="FP32 / FP16 비교 (발견 3)")
    parser.add_argument(
        "--pose-budget", action="store_true", help="Pose 전역 예산 유/무 비교 (발견 1)"
    )
    parser.add_argument("--json", default=None, help="결과를 JSON 파일로도 저장")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        # 벤치는 재현성이 생명이라 카메라 입력을 허용하지 않는다.
        print(f"입력 영상이 없습니다: {source}", file=sys.stderr)
        print("벤치는 고정된 영상 파일로만 실행합니다(카메라는 매번 조건이 달라집니다).", file=sys.stderr)
        return 1

    base = load_config(Path(args.config))
    cases = build_cases(args, base)

    rows = []
    for i, (name, config) in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {name} 실행 중...", file=sys.stderr)
        rows.append(
            run_case(
                name,
                config,
                source=str(source),
                device=args.device,
                max_frames=args.max_frames,
                warmup=args.warmup,
            )
        )

    print_table(rows)

    if args.json:
        Path(args.json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

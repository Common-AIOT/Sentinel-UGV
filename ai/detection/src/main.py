"""CLI entry point.

사용 예:
    python -m src.main --source data/pose_test/lying.mp4 --output runs/pipeline/lying
    python -m src.main --source 0 --model models/best.pt --max-frames 300
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .pipeline import InferencePipeline

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "pipeline.yaml"


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
    with path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentinel UGV 객체탐지 파이프라인 "
        "(Detect → ByteTrack → Pose → 자세 판정 → 이벤트 기록)",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="입력 영상 경로. 웹캠은 '0' 같은 장치 인덱스를 쓴다.",
    )
    parser.add_argument(
        "--output",
        default="runs/pipeline",
        help="결과 출력 디렉터리 (기본: runs/pipeline)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"파이프라인 설정 파일 (기본: {DEFAULT_CONFIG})",
    )
    # 모델 경로를 인자로 받는다. 파인튜닝 가중치 교체가 이 인자 하나로 끝나야 한다
    # (AGENTS.md §26, 게이트 11번).
    parser.add_argument(
        "--model",
        default=None,
        help="Detect 모델 경로. 미지정 시 설정 파일 값을 쓴다.",
    )
    parser.add_argument(
        "--pose-model",
        default=None,
        help="Pose 모델 경로. 미지정 시 설정 파일 값을 쓴다.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="추론 장치 (예: 0, cpu). 미지정 시 Ultralytics 기본값.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=None,
        help="Detect confidence threshold 덮어쓰기",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="처리할 최대 프레임 수 (스모크 테스트용)",
    )
    parser.add_argument(
        "--frame-log",
        action="store_true",
        help="프레임 단위 JSONL 로그를 함께 기록한다 (용량 주의)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="overlay 미리보기 창을 띄운다. 종료는 q 또는 ESC.",
    )
    parser.add_argument(
        "--no-run-subdir",
        action="store_true",
        help="실행별 타임스탬프 하위 폴더를 만들지 않고 --output에 바로 기록한다.",
    )
    camera = parser.add_argument_group("카메라 입력 (--source가 장치 번호일 때만 적용)")
    camera.add_argument(
        "--width", type=int, default=None, help="카메라 가로 해상도 (예: 1920)"
    )
    camera.add_argument(
        "--height", type=int, default=None, help="카메라 세로 해상도 (예: 1080)"
    )
    camera.add_argument(
        "--camera-backend",
        choices=["auto", "dshow", "msmf", "v4l2", "gstreamer", "any"],
        default=None,
        help="OpenCV 카메라 백엔드. auto는 Windows→dshow, Linux/Jetson→v4l2로 자동 선택.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except (OSError, yaml.YAMLError) as exc:
        print(f"[main] 설정을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 1

    if args.conf is not None:
        config["detector"]["confidence"] = args.conf
    if args.frame_log:
        config["output"]["write_frame_log"] = True

    config.setdefault("camera", {})
    if args.width is not None:
        config["camera"]["width"] = args.width
    if args.height is not None:
        config["camera"]["height"] = args.height
    if args.camera_backend is not None:
        config["camera"]["backend"] = args.camera_backend

    # 실행마다 타임스탬프 하위 폴더를 만든다.
    # 이렇게 하지 않으면 events/ 이미지는 누적되는데 events.jsonl은 덮어써져서
    # 이미지와 기록이 어긋난다. 관제 보고 증빙이므로 실행 단위로 분리한다.
    output_dir = Path(args.output)
    if not args.no_run_subdir:
        output_dir = output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[main] 출력 디렉터리를 만들 수 없습니다: {output_dir} ({exc})", file=sys.stderr)
        return 1

    # 웹캠 장치 인덱스는 정수로 넘겨야 OpenCV가 인식한다.
    source: str | int = args.source
    if isinstance(source, str) and source.isdigit():
        source = int(source)

    try:
        with InferencePipeline(
            config,
            output_dir=output_dir,
            detector_model=args.model,
            pose_model=args.pose_model,
            device=args.device,
        ) as pipeline:
            stats = pipeline.run_video(
                source, max_frames=args.max_frames, show=args.show
            )
    except RuntimeError as exc:
        print(f"[main] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # 중단해도 그때까지의 JSONL과 이벤트 이미지는 이미 flush되어 남는다.
        print(f"\n[main] 사용자 중단. 결과는 {output_dir.resolve()} 에 남아 있습니다.", file=sys.stderr)
        return 130

    print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n결과 디렉터리: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ROS2 CompressedImage 구독 실행 진입점 (명세 9.6장·25.7절 최종 통합 구조).

카메라를 직접 열지 않고 `usb_cam`이 발행하는 `/camera/image_raw/compressed`를
구독해 추론 파이프라인을 돌린다. 카메라 장치는 usb_cam이 단독 점유하므로
스트리밍과 AI를 같은 카메라로 동시에 구동할 수 있다(카메라 단일 오픈 원칙).

확정된 사람 후보는 `/perception/person_candidates`로 발행한다
(S15P11A301-133 계약, `common/schemas/person-candidates.schema.json`).
encounter 권한은 Mission Manager에 있으므로(명세 26.1) encounter는 발행하지
않는다. 결과는 src.main과 동일하게 events.jsonl과 이벤트 이미지로도 기록한다.

사용 예 (ai/detection에서, ROS2 환경 source 후):
    source /opt/ros/humble/setup.bash
    python -m src.ros_main --config configs/pipeline.jetson.yaml \\
        --topic /camera/image_raw/compressed --output runs/jetson_topic \\
        --max-seconds 30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import yaml

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (
        QoSDurabilityPolicy,
        QoSHistoryPolicy,
        QoSProfile,
        QoSReliabilityPolicy,
    )
    from sensor_msgs.msg import CompressedImage
    from std_msgs.msg import String
except ImportError as exc:  # pragma: no cover - ROS 미설치 환경
    raise SystemExit(
        "rclpy를 가져올 수 없습니다. ROS2 환경을 먼저 source 하세요: "
        "source /opt/ros/humble/setup.bash"
    ) from exc

from .candidates import candidates_body, confirmed_candidates, format_utc
from .main import DEFAULT_CONFIG, load_config
from .pipeline import InferencePipeline


class DetectionTopicRunner(Node):
    """CompressedImage를 받아 프레임 단위로 파이프라인에 넘긴다.

    콜백 안에서 바로 추론한다. QoS depth=1이라 추론(수십~수백 ms) 동안 도착한
    프레임은 DDS가 최신 것만 남기므로, 콜백이 끝나면 항상 가장 새 프레임을
    받는다. 오래된 프레임을 큐에 쌓아 처리하는 것보다 "사람이 지금 어디
    있는지"에 맞는 동작이다(person_detector_node와 같은 이유).
    """

    def __init__(
        self,
        pipeline: InferencePipeline,
        *,
        topic: str,
        max_frames: int | None,
        candidates_topic: str | None = None,
        candidates_period: float = 0.2,
        confirm_seconds: float = 1.0,
        stale_seconds: float = 1.0,
    ) -> None:
        super().__init__("ai_detection_wrapper")
        self.pipeline = pipeline
        self.topic = topic
        self.max_frames = max_frames
        self.confirm_seconds = confirm_seconds
        self.stale_seconds = stale_seconds

        self.done = False
        self.frame_index = 0
        self.decode_failures = 0
        self.first_frame_monotonic: float | None = None
        self.last_frame_monotonic: float | None = None
        # 최근 프레임 처리 시간. run_video와 동일하게 실측 FPS로
        # 트래커 기억 길이를 맞추는 데 쓴다.
        self._recent: list[float] = []
        # 마지막으로 처리한 프레임에서 확정된 후보 스냅샷.
        self._candidates: list[dict] = []
        self._observed_at: str | None = None
        self._frame_id: str | None = None

        # usb_cam은 RELIABLE로 발행하므로 BEST_EFFORT 구독이 호환된다.
        # depth=1: 추론이 프레임보다 느릴 때 항상 최신 프레임만 처리한다.
        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(CompressedImage, topic, self._on_frame, qos)
        self.get_logger().info(f"{topic} 구독 시작 (depth=1, BEST_EFFORT)")

        # 후보 발행. 후보가 없어도 빈 배열을 계속 보내야 mission_manager가
        # "사람 없음"과 "탐지 노드 죽음"을 구별한다(133 계약).
        # mission_manager가 BEST_EFFORT로 구독하므로 맞춘다.
        self.candidates_pub = None
        if candidates_topic:
            self.candidates_pub = self.create_publisher(
                String,
                candidates_topic,
                QoSProfile(
                    reliability=QoSReliabilityPolicy.BEST_EFFORT,
                    history=QoSHistoryPolicy.KEEP_LAST,
                    depth=5,
                ),
            )
            self.create_timer(candidates_period, self._on_publish_tick)
            self.get_logger().info(
                f"{candidates_topic} 발행 시작 "
                f"(주기 {candidates_period}s, 확정 기준 {confirm_seconds}s)"
            )

    def _on_frame(self, message: CompressedImage) -> None:
        if self.done:
            return
        loop_start = time.monotonic()
        if self.first_frame_monotonic is None:
            self.first_frame_monotonic = loop_start
            self.get_logger().info("첫 프레임 수신. 추론을 시작한다 (워밍업 수 초)")

        # CompressedImage는 이미 인코딩된 바이트라 cv2.imdecode로 충분하다.
        frame = cv2.imdecode(
            np.frombuffer(message.data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            self.decode_failures += 1
            self.get_logger().warn("프레임 디코딩 실패. format을 확인한다")
            return

        # 라이브 입력이므로 run_video의 카메라 경로와 동일하게 벽시계 기준
        # 경과 시간을 쓴다. 토픽은 프레임 드롭이 있어 frame_index/fps가
        # 실제 경과 시간과 어긋난다.
        timestamp_sec = loop_start - self.first_frame_monotonic
        result = self.pipeline.process_frame(
            frame, self.frame_index, timestamp_sec, self.topic
        )
        # 후보 스냅샷을 갱신한다. observedAt은 발행 시각이 아니라 이 프레임의
        # 관측 시각이다 — mission_manager가 확정 시각으로 쓰고 녹화 노드가
        # 사전 3초 조각을 찾는 기준이다(133 계약).
        self._candidates = confirmed_candidates(result.persons, self.confirm_seconds)
        self._observed_at = format_utc(datetime.now(timezone.utc))
        self._frame_id = str(self.frame_index)
        self.last_frame_monotonic = loop_start
        self.frame_index += 1

        self._recent.append(time.monotonic() - loop_start)
        if len(self._recent) > 30:
            self._recent.pop(0)
        if len(self._recent) >= 5:
            # run_video와 같은 로직을 재사용한다. 실측 FPS에 맞춰
            # "10초 기억"이 항상 10초가 되게 한다.
            self.pipeline._sync_track_buffer(len(self._recent) / sum(self._recent))

        if self.max_frames is not None and self.frame_index >= self.max_frames:
            self.done = True

    def _on_publish_tick(self) -> None:
        """후보가 없어도 발행한다. 추론이 멈추면 오래된 후보를 보내지 않는다.

        마지막 프레임이 stale_seconds보다 오래됐으면 빈 배열로 바꾼다.
        추론이 멈췄는데 마지막 후보를 계속 재발행하면 mission_manager가
        사라진 사람을 계속 있는 것으로 판단한다.
        """
        now = time.monotonic()
        stale = (
            self.last_frame_monotonic is None
            or now - self.last_frame_monotonic > self.stale_seconds
        )
        if stale:
            body = candidates_body([], format_utc(datetime.now(timezone.utc)))
        else:
            body = candidates_body(
                self._candidates, self._observed_at, frame_id=self._frame_id
            )
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.candidates_pub.publish(message)

    def log_progress(self) -> None:
        if self.first_frame_monotonic is None:
            self.get_logger().warn(
                f"{self.topic} 프레임을 아직 받지 못했다. "
                "발행 노드(usb_cam)와 토픽 이름을 확인한다"
            )
            return
        fps = len(self._recent) / sum(self._recent) if self._recent else 0.0
        stats = self.pipeline.stats
        self.get_logger().info(
            f"처리 {self.frame_index}프레임 {fps:.1f}FPS "
            f"사람프레임 {stats.frames_with_person} "
            f"확정후보 {len(self._candidates)}명 "
            f"이벤트 {stats.events} 디코딩실패 {self.decode_failures}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentinel UGV 객체탐지 파이프라인 ROS2 토픽 실행 "
        "(usb_cam CompressedImage 구독)",
    )
    parser.add_argument(
        "--topic",
        default="/camera/image_raw/compressed",
        help="구독할 sensor_msgs/CompressedImage 토픽 (기본: /camera/image_raw/compressed)",
    )
    parser.add_argument(
        "--output",
        default="runs/ros2",
        help="결과 출력 디렉터리 (기본: runs/ros2)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help=f"파이프라인 설정 파일 (기본: {DEFAULT_CONFIG})",
    )
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
        "--max-seconds",
        type=float,
        default=None,
        help="최대 실행 시간(초). 프레임 수신 여부와 무관하게 시작 시점부터 잰다.",
    )
    parser.add_argument(
        "--candidates-topic",
        default="/perception/person_candidates",
        help="확정 후보를 발행할 토픽 (기본: /perception/person_candidates)",
    )
    parser.add_argument(
        "--no-candidates",
        action="store_true",
        help="후보 발행을 끈다 (단독 벤치 검증용)",
    )
    parser.add_argument(
        "--candidates-period",
        type=float,
        default=0.2,
        help="후보 발행 주기(초). 후보가 없어도 이 주기로 빈 배열을 발행한다 (기본: 0.2)",
    )
    parser.add_argument(
        "--confirm-seconds",
        type=float,
        default=1.0,
        help="후보 확정에 필요한 안정 관측 시간(초, 명세 25.2) (기본: 1.0)",
    )
    parser.add_argument(
        "--frame-log",
        action="store_true",
        help="프레임 단위 JSONL 로그를 함께 기록한다 (용량 주의)",
    )
    parser.add_argument(
        "--no-run-subdir",
        action="store_true",
        help="실행별 타임스탬프 하위 폴더를 만들지 않고 --output에 바로 기록한다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(Path(args.config))
    except (OSError, yaml.YAMLError) as exc:
        print(f"[ros_main] 설정을 읽을 수 없습니다: {exc}", file=sys.stderr)
        return 1

    if args.conf is not None:
        config["detector"]["confidence"] = args.conf
    if args.frame_log:
        config["output"]["write_frame_log"] = True

    # src.main과 동일한 이유로 실행마다 타임스탬프 하위 폴더를 만든다.
    output_dir = Path(args.output)
    if not args.no_run_subdir:
        output_dir = output_dir / datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[ros_main] 출력 디렉터리를 만들 수 없습니다: {output_dir} ({exc})", file=sys.stderr)
        return 1

    rclpy.init()
    interrupted = False
    try:
        with InferencePipeline(
            config,
            output_dir=output_dir,
            detector_model=args.model,
            pose_model=args.pose_model,
            device=args.device,
        ) as pipeline:
            node = DetectionTopicRunner(
                pipeline,
                topic=args.topic,
                max_frames=args.max_frames,
                candidates_topic=None if args.no_candidates else args.candidates_topic,
                candidates_period=args.candidates_period,
                confirm_seconds=args.confirm_seconds,
            )
            loop_start = time.monotonic()
            last_log = loop_start
            try:
                while rclpy.ok() and not node.done:
                    rclpy.spin_once(node, timeout_sec=0.2)
                    now = time.monotonic()
                    if args.max_seconds is not None and now - loop_start >= args.max_seconds:
                        break
                    if now - last_log >= 10.0:
                        node.log_progress()
                        last_log = now
            except KeyboardInterrupt:
                interrupted = True
            finally:
                # 통계를 마무리한다. run_video의 finally와 같은 역할이다.
                if node.first_frame_monotonic is not None:
                    pipeline.stats.elapsed_sec = (
                        time.monotonic() - node.first_frame_monotonic
                    )
                pipeline.stats.track_buffer_frames = pipeline._track_buffer_frames
                node.destroy_node()
    except RuntimeError as exc:
        print(f"[ros_main] {exc}", file=sys.stderr)
        return 1
    finally:
        if rclpy.ok():
            rclpy.shutdown()

    if interrupted:
        print(
            f"\n[ros_main] 사용자 중단. 결과는 {output_dir.resolve()} 에 남아 있습니다.",
            file=sys.stderr,
        )
        return 130

    print(json.dumps(pipeline.stats.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n결과 디렉터리: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

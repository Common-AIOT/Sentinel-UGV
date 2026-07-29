#!/usr/bin/env python3
"""사람 탐지 노드 (S15P11A301-136, 명세 25.1·25.2).

`/perception/person_candidates`를 발행한다. 계약은
`common/schemas/person-candidates.schema.json`이며 S15P11A301-133에서 확정했다.

## encounter를 만들지 않는다

`encounterId`를 발급하지 않고 `phase`도 정하지 않는다. 26.1이 Mission Manager를
유일한 권한자로 정했다. 이 노드가 `/perception/encounter`를 직접 발행하면 발행자가
여럿이 되어 한 사람의 이벤트가 쪼개진다.

여기서는 "사람이 보인다"는 사실만 알린다.

## `.venv` 파이썬으로 실행한다

`ultralytics`와 `torch`가 프로젝트 `.venv`에만 있고 ROS는 시스템 파이썬에 있다.
`ros2 run`은 쓸 수 없다 — colcon이 만든 실행 스크립트의 shebang이
`/usr/bin/python3`로 박히기 때문이다.

    source install/setup.bash
    .venv/bin/python -m sentinel_perception.person_detector_node --ros-args ...

`.venv`가 `include-system-site-packages = false`인데도 `rclpy`가 보이는 이유는 ROS가
`PYTHONPATH`를 설정하고 venv가 그것을 무시하지 않기 때문이다. `numpy`는 venv
것(1.26.4)이 우선이라 런북의 `numpy<2` 제약도 지켜진다.

launch 파일이 이 실행을 감싸므로 평소에는 `ros2 launch`로 띄운다.

## 모든 프레임을 추론하지 않는다

카메라는 30fps인데 추론은 약 47ms다. 전부 처리하려 하면 큐가 밀리고 스트리밍이
GPU를 못 쓴다. 32장이 관제 영상을 우선순위로 정했다.

25.2의 확정 기준이 "약 1초 동안 최소 관측 횟수"이므로 5Hz로도 창 안에 3~5번
관측된다. `inference_period_seconds`로 조절한다.

## 후보가 없어도 발행한다

빈 `candidates` 배열을 보낸다. 발행을 멈추면 `mission_manager`가 "사람이 사라진 것"과
"탐지 노드가 죽은 것"을 구별할 수 없고, 후자일 때 진행 중 이벤트가 조용히 종료된다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
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

from .candidate_filter import CandidateFilter
from .tracker import IouTracker


def format_utc(moment: datetime) -> str:
    """person-candidates.schema.json의 pattern에 맞춘다."""
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec='milliseconds')
        .replace('+00:00', 'Z')
    )


class PersonDetectorNode(Node):
    def __init__(self) -> None:
        super().__init__('person_detector')

        self.declare_parameter('camera_topic', '/camera/image_raw/compressed')
        self.declare_parameter('candidates_topic', '/perception/person_candidates')
        self.declare_parameter('model_path', 'jetson/models/yolo26n.pt')
        self.declare_parameter('image_size', 640)
        self.declare_parameter('confidence', 0.5)
        self.declare_parameter('device', 0)
        # 추론 주기. 카메라 30fps를 전부 처리하지 않는다. 0.2초면 5Hz다.
        self.declare_parameter('inference_period_seconds', 0.2)
        # 발행 주기. 추론과 별개로 둔다. 후보가 없을 때도 계속 발행해야 하고,
        # 추론이 느려지거나 실패해도 "사람 없음"이 관제 경로에 계속 흘러야 한다.
        self.declare_parameter('publish_period_seconds', 0.2)
        self.declare_parameter('iou_threshold', 0.3)
        self.declare_parameter('max_misses', 3)
        self.declare_parameter('window_seconds', 1.0)
        self.declare_parameter('min_observations', 3)

        self.tracker = IouTracker(
            iou_threshold=float(self._param('iou_threshold')),
            max_misses=int(self._param('max_misses')),
        )
        self.filter = CandidateFilter(
            window_seconds=float(self._param('window_seconds')),
            min_observations=int(self._param('min_observations')),
        )

        # 모델 로딩은 수 초 걸린다. 실패하면 노드를 띄우지 않는다. 탐지 없이
        # 계속 도는 것보다 명확히 죽는 편이 낫다 — 관제는 빈 배열을 "사람 없음"으로
        # 해석하므로, 모델이 없는데 빈 배열을 보내면 조용히 아무도 못 찾는다.
        from .detector import PersonDetector

        self.get_logger().info(
            f'모델을 불러온다: {self._param("model_path")} (수 초 걸린다)'
        )
        self.detector = PersonDetector(
            self._param('model_path'),
            image_size=int(self._param('image_size')),
            confidence=float(self._param('confidence')),
            device=self._param('device'),
        )

        # 프레임은 최신 것만 쓴다. depth=1이고 BEST_EFFORT다.
        #
        # 추론이 프레임보다 느리므로 큐를 쌓으면 오래된 프레임을 처리하게 된다.
        # 사람이 지금 어디 있는지가 중요하고 0.5초 전 위치는 쓸모가 없다.
        #
        # usb_cam이 RELIABLE로 발행하므로 BEST_EFFORT 구독은 호환된다. 반대는
        # 매칭되지 않는다(S15P11A301-128에서 /scan으로 겪었다).
        camera_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            CompressedImage,
            self._param('camera_topic'),
            self._on_frame,
            camera_qos,
        )

        # 후보는 프레임마다 오고 하나 놓쳐도 다음이 온다. mission_manager가
        # BEST_EFFORT로 구독하므로 맞춘다.
        self.candidates_pub = self.create_publisher(
            String,
            self._param('candidates_topic'),
            QoSProfile(
                reliability=QoSReliabilityPolicy.BEST_EFFORT,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=5,
            ),
        )

        self._latest_frame: np.ndarray | None = None
        self._latest_frame_at: float | None = None
        self._last_inference_at = 0.0
        self._frames_received = 0
        self._frames_inferred = 0
        self._frames_dropped = 0
        self._last_latency_ms = 0.0
        self._confirmed_count = 0

        self.create_timer(
            float(self._param('inference_period_seconds')), self._on_inference_tick
        )
        self.create_timer(
            float(self._param('publish_period_seconds')), self._on_publish_tick
        )
        # 실측치를 주기적으로 남긴다. 추론이 스트리밍을 밀어내는지 판단할 근거다.
        self.create_timer(10.0, self._log_stats)

        self.get_logger().info(
            f'person_detector 시작. '
            f'{self._param("camera_topic")} → {self._param("candidates_topic")} '
            f'추론 {1 / float(self._param("inference_period_seconds")):.1f}Hz '
            f'imgsz={self._param("image_size")} conf={self._param("confidence")}'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ------------------------------------------------------------------
    # 입력
    # ------------------------------------------------------------------

    def _on_frame(self, message: CompressedImage) -> None:
        """최신 프레임만 들고 있는다. 여기서 추론하지 않는다.

        콜백에서 추론하면 47ms 동안 실행기가 막혀 다른 콜백과 타이머가 밀린다.
        발행 타이머까지 밀리면 "사람 없음"조차 못 보낸다.
        """
        self._frames_received += 1
        # cv_bridge를 쓰지 않는다. CompressedImage는 이미 인코딩된 바이트이므로
        # cv2.imdecode로 충분하고, 의존성이 하나 줄어든다.
        import cv2

        buffer = np.frombuffer(message.data, dtype=np.uint8)
        frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if frame is None:
            self.get_logger().warn('프레임 디코딩 실패. 형식을 확인한다')
            return
        if self._latest_frame is not None:
            # 앞 프레임을 추론하지 못한 채 새 것이 왔다. 의도한 동작이지만
            # 얼마나 버리는지 알아야 추론 주기를 정할 수 있다.
            self._frames_dropped += 1
        self._latest_frame = frame
        self._latest_frame_at = self._now()

    # ------------------------------------------------------------------
    # 추론과 발행
    # ------------------------------------------------------------------

    def _on_inference_tick(self) -> None:
        frame = self._latest_frame
        if frame is None:
            return
        # 같은 프레임을 두 번 추론하지 않는다.
        self._latest_frame = None

        started = self._now()
        try:
            detections = self.detector.detect(frame)
        except Exception as error:  # noqa: BLE001
            # 추론 실패로 노드를 죽이지 않는다. GPU 메모리 부족은 일시적일 수
            # 있고, 죽으면 발행이 멈춰 mission_manager가 진행 중 이벤트를
            # 잘못 종료한다.
            self.get_logger().error(
                f'추론 실패: {type(error).__name__}: {error}. 다음 프레임에 재시도한다'
            )
            return

        self._frames_inferred += 1
        self._last_latency_ms = (self._now() - started) * 1000
        self._last_inference_at = started

        tracks = self.tracker.update(detections, started)
        for reason in self.filter.last_rejections:
            self.get_logger().info(f'급변으로 확정 보류: {reason}')
        self._candidates = self.filter.confirm(self.tracker.visible(), started)
        self._confirmed_count = len(self._candidates)
        del tracks  # 사용하지 않는다. visible()로 다시 고른다.

    def _on_publish_tick(self) -> None:
        """후보가 없어도 발행한다.

        추론이 한 번도 성공하지 않았어도 빈 배열을 보낸다. 발행이 없는 것과
        사람이 없는 것을 mission_manager가 구별할 수 없기 때문이다.
        """
        candidates = getattr(self, '_candidates', [])
        body = {
            'observedAt': format_utc(datetime.now(timezone.utc)),
            'candidates': [candidate.as_dict() for candidate in candidates],
            'frameId': None,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.candidates_pub.publish(message)

    def _log_stats(self) -> None:
        if self._frames_received == 0:
            self.get_logger().warn(
                f'카메라 프레임을 한 번도 받지 못했다. '
                f'{self._param("camera_topic")} 발행 여부와 QoS를 확인한다'
            )
            return
        self.get_logger().info(
            f'수신 {self._frames_received}프레임 '
            f'추론 {self._frames_inferred}회 '
            f'버림 {self._frames_dropped}프레임 '
            f'지연 {self._last_latency_ms:.0f}ms '
            f'확정 {self._confirmed_count}명 '
            f'추적 {len(self.tracker.tracks)}개'
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = PersonDetectorNode()
    except (FileNotFoundError, ImportError) as error:
        # 모델이나 torch가 없으면 여기서 끝낸다. 탐지 없이 빈 배열만 보내면
        # 관제가 "사람 없음"으로 해석해 아무도 못 찾는 것을 모른다.
        print(f'person_detector를 시작할 수 없다: {error}')
        if rclpy.ok():
            rclpy.shutdown()
        raise SystemExit(1) from error
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

"""탐사 노드 — ROS 껍데기 (S15P11A301-172).

판단은 전부 순수 모듈(frontier·coverage·selector·sweep)에 있고, 여기는 구독·
발행·시계·TF 만 다룬다. 그래서 이 파일에는 시험이 없다 — 시험할 논리를 여기
두지 않는 것이 규칙이다.

## 주행은 Navigator 인터페이스 뒤에 있다

Nav2 연결(S15P11A301-235)이 아직 없으므로 기본은 `NullNavigator` 다 — 목표를
`~/goal` 로 발행하고 로그만 남긴다. 235 가 `Nav2Navigator` 를 꽂으면 이 노드는
바뀌지 않는다. 축소판(순찰 시퀀스)도 같은 자리(`select_goal`)에서 갈아끼운다.

## 상태 게이트

`/mission/status` 의 `movementAllowed` 만 본다. 상태 판단을 여기서 다시 하지
않는다(26.2) — PAUSED·ESTOP·MANUAL 에서 멈추는 근거를 두 곳에 두면 언젠가
한쪽만 바뀐다. 상태 토픽이 `status_stale_s` 동안 없으면 **멈춘다.** 마지막
값을 믿고 계속 달리면 mission_manager 가 죽었을 때 아무도 로봇을 세우지 않는다.
"""

from __future__ import annotations

import json
import math

import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

from .coverage import CameraCoverage, observation_candidates
from .frontier import cluster_alive, extract_frontiers
from .grid import GridInfo
from .selector import Candidate, Commitment, Weights, compute_gains, score
from .sweep import needed_sectors, plan_sweep


class NullNavigator:
    """Nav2 가 붙기 전의 자리 표시자. 목표를 받기만 하고 이동은 없다.

    도달 콜백을 부르지 않으므로 노드는 SELECT 상태에 머문다 — 그것이 정직한
    표현이다. 로그에 목표가 계속 찍히면 "선택은 되는데 주행이 없다"가 보인다.
    """

    def __init__(self, node: Node) -> None:
        self._node = node

    def send_goal(self, x: float, y: float, yaw: float) -> None:
        self._node.get_logger().info(
            f'목표 선택 (Nav2 미연결 — S15P11A301-235 대기): ({x:.2f}, {y:.2f}, {math.degrees(yaw):.0f}°)'
        )

    def cancel(self) -> None:
        pass


class ExplorationNode(Node):
    """EXPLORING 에서 목표를 고르고, 아니면 멈춘다."""

    def __init__(self) -> None:
        super().__init__('exploration')

        self.declare_parameter('select_period_s', 2.0)
        self.declare_parameter('coverage_period_s', 0.5)
        self.declare_parameter('status_stale_s', 3.0)
        self.declare_parameter('camera_hfov_deg', 52.0)   # BRIO 100 대각 58° 의 수평 환산. 실측 전 잠정
        self.declare_parameter('detect_range_m', 5.0)      # 누운 사람 기준. 실측 전 잠정
        self.declare_parameter('max_radius_m', 12.0)
        self.declare_parameter('min_frontier_cells', 6)
        self.declare_parameter('coverage_done_m2', 1.5)
        self.declare_parameter('sweep_sectors', 9)
        self.declare_parameter('max_angular_for_coverage_radps', 0.2)
        self.declare_parameter('breadcrumb_spacing_m', 0.5)

        self._coverage = CameraCoverage()
        self._weights = Weights()
        self._grid: np.ndarray | None = None
        self._info: GridInfo | None = None
        self._movement_allowed = False
        self._status_at: float | None = None
        self._home: tuple[float, float] | None = None
        self._commitment: Commitment | None = None
        self._visit_history: list[tuple[float, float]] = []
        # 23.6 복귀 2단(빵조각)의 입력. 여기서 공짜로 생긴다.
        self._breadcrumbs: list[tuple[float, float]] = []
        self._last_pose: tuple[float, float, float] | None = None
        self._last_pose_time: float | None = None

        self._navigator = NullNavigator(self)

        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # 두 토픽 모두 latched(transient_local) 발행이다 — slam_toolbox 의 /map,
        # mission_manager 의 /mission/status(상태 변화 때만 나온다). volatile 로
        # 구독하면 이미 발행된 값을 못 받아, 로봇이 서 있는 동안(지도 갱신 없음)
        # 이 노드는 지도도 상태도 영영 모른다. 실기동 스모크에서 그렇게 물렸다.
        latched = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(OccupancyGrid, '/map', self._on_map, latched)
        self.create_subscription(String, '/mission/status', self._on_status, latched)
        self._goal_pub = self.create_publisher(PoseStamped, '~/goal', 1)
        self._status_pub = self.create_publisher(String, '~/status', 1)

        self.create_timer(self.get_parameter('coverage_period_s').value, self._update_coverage)
        self.create_timer(self.get_parameter('select_period_s').value, self._select)

    # ── 입력 ────────────────────────────────────────────────────────────────

    def _on_map(self, message: OccupancyGrid) -> None:
        self._grid = np.asarray(message.data, dtype=np.int8).reshape(
            message.info.height, message.info.width
        )
        self._info = GridInfo(
            resolution=message.info.resolution,
            origin_x=message.info.origin.position.x,
            origin_y=message.info.origin.position.y,
            width=message.info.width,
            height=message.info.height,
        )

    def _on_status(self, message: String) -> None:
        try:
            body = json.loads(message.data)
        except ValueError:
            return
        self._movement_allowed = bool(body.get('movementAllowed', False))
        self._status_at = self._now()
        if self._home is None and body.get('state') == 'EXPLORING':
            pose = self._pose()
            if pose is not None:
                self._home = (pose[0], pose[1])
                self.get_logger().info(f'home pose 저장: ({pose[0]:.2f}, {pose[1]:.2f})')

    # ── 주기 작업 ───────────────────────────────────────────────────────────

    def _update_coverage(self) -> None:
        """카메라 커버리지 갱신. 각속도 게이트가 여기 있다.

        회전 중 프레임은 블러로 탐지 신뢰도가 떨어지므로 본 것으로 치지
        않는다. 직진 주행 중에는 커버리지가 공짜로 쌓인다.
        """
        if self._grid is None or self._info is None:
            return
        pose = self._pose()
        if pose is None:
            return
        x, y, yaw = pose
        now = self._now()

        angular_ok = True
        if self._last_pose is not None and self._last_pose_time is not None:
            dt = now - self._last_pose_time
            if dt > 1e-3:
                d_yaw = abs(math.atan2(math.sin(yaw - self._last_pose[2]),
                                       math.cos(yaw - self._last_pose[2])))
                angular_ok = (
                    d_yaw / dt
                    < self.get_parameter('max_angular_for_coverage_radps').value
                )
            self._maybe_drop_breadcrumb(x, y)
        self._last_pose = (x, y, yaw)
        self._last_pose_time = now

        if angular_ok:
            self._coverage.mark_visible(
                self._grid, self._info, x, y, yaw,
                hfov_rad=math.radians(self.get_parameter('camera_hfov_deg').value),
                range_m=self.get_parameter('detect_range_m').value,
            )

    def _select(self) -> None:
        if not self._movement_gate_open():
            self._navigator.cancel()
            self._commitment = None
            self._publish_status('HOLD')
            return
        if self._grid is None or self._info is None:
            self._publish_status('WAIT_MAP')
            return
        pose = self._pose()
        if pose is None:
            return
        x, y, _ = pose
        home_x, home_y = self._home if self._home else (x, y)

        # 소스 A: frontier
        clusters = extract_frontiers(
            self._grid, self._info,
            min_cells=self.get_parameter('min_frontier_cells').value,
            home_x=home_x, home_y=home_y,
            max_radius_m=self.get_parameter('max_radius_m').value,
        )
        candidates = [
            Candidate(x=c.rep_x, y=c.rep_y, kind='frontier', payload=c) for c in clusters
        ]

        # 소스 B: 관측 후보. frontier 소진은 지도 완성이지 수색 완료가 아니다.
        unseen = self._coverage.unseen_free(self._grid, self._info)
        if not candidates:
            candidates = [
                Candidate(x=ox, y=oy, kind='observation')
                for ox, oy in observation_candidates(unseen)
            ]

        if not candidates:
            self._navigator.cancel()
            self._commitment = None
            self._publish_status('DONE')
            return

        for candidate in candidates:
            candidate.map_gain_m2, candidate.camera_gain_m2 = compute_gains(
                self._grid, self._info, self._coverage, candidate.x, candidate.y
            )
        best = max(
            candidates,
            key=lambda c: score(c, self._weights, from_x=x, from_y=y, history=self._visit_history),
        )
        best_score = score(best, self._weights, from_x=x, from_y=y, history=self._visit_history)
        now = self._now()

        if self._commitment is not None:
            committed = self._commitment.candidate
            vanished = (
                committed.kind == 'frontier'
                and committed.payload is not None
                and not cluster_alive(self._grid, committed.payload)
            )
            if not vanished and not self._commitment.should_replace(best_score, now):
                self._publish_status('DRIVING', unseen_count=len(unseen))
                return

        self._commitment = Commitment(candidate=best, committed_score=best_score, committed_at=now)
        self._visit_history.append((best.x, best.y))
        goal_yaw = self._goal_yaw(best, unseen)
        self._navigator.send_goal(best.x, best.y, goal_yaw)
        self._publish_goal(best.x, best.y, goal_yaw)
        self._publish_status('DRIVING', unseen_count=len(unseen))

    # ── 보조 ────────────────────────────────────────────────────────────────

    def _goal_yaw(self, candidate: Candidate, unseen: list[tuple[float, float]]) -> float:
        """도착 자세의 yaw 를 미관측 방향으로 잡는다 — 도착 즉시 정면이 유효 관측이다."""
        sectors = needed_sectors(
            candidate.x, candidate.y, unseen,
            n_sectors=self.get_parameter('sweep_sectors').value,
            range_m=self.get_parameter('detect_range_m').value,
        )
        plan = plan_sweep(0.0, sectors)
        return plan[0] if plan else 0.0

    def _movement_gate_open(self) -> bool:
        if not self._movement_allowed:
            return False
        if self._status_at is None:
            return False
        return self._now() - self._status_at < self.get_parameter('status_stale_s').value

    def _maybe_drop_breadcrumb(self, x: float, y: float) -> None:
        spacing = self.get_parameter('breadcrumb_spacing_m').value
        if not self._breadcrumbs:
            self._breadcrumbs.append((x, y))
            return
        last_x, last_y = self._breadcrumbs[-1]
        if math.hypot(x - last_x, y - last_y) >= spacing:
            self._breadcrumbs.append((x, y))

    def _pose(self) -> tuple[float, float, float] | None:
        try:
            transform = self._tf_buffer.lookup_transform('map', 'base_footprint', rclpy.time.Time())
        except Exception:  # noqa: BLE001 — TF 예외 계층이 넓고, 없음은 정상 상황이다
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        return translation.x, translation.y, yaw

    def _publish_goal(self, x: float, y: float, yaw: float) -> None:
        message = PoseStamped()
        message.header.frame_id = 'map'
        message.header.stamp = self.get_clock().now().to_msg()
        message.pose.position.x = x
        message.pose.position.y = y
        message.pose.orientation.z = math.sin(yaw / 2.0)
        message.pose.orientation.w = math.cos(yaw / 2.0)
        self._goal_pub.publish(message)

    def _publish_status(self, state: str, *, unseen_count: int = 0) -> None:
        body = {
            'state': state,
            'unseenCount': unseen_count,
            'coverageCells': self._coverage.seen_count,
            'breadcrumbs': len(self._breadcrumbs),
            'goal': (
                {'x': self._commitment.candidate.x, 'y': self._commitment.candidate.y,
                 'kind': self._commitment.candidate.kind}
                if self._commitment else None
            ),
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self._status_pub.publish(message)

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9


def main() -> None:
    rclpy.init()
    node = ExplorationNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()

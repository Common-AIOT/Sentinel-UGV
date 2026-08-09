"""탐사 노드 — ROS 껍데기 (S15P11A301-172).

판단은 전부 순수 모듈(frontier·coverage·selector·sweep)에 있고, 여기는 구독·
발행·시계·TF 만 다룬다. 그래서 이 파일에는 시험이 없다 — 시험할 논리를 여기
두지 않는 것이 규칙이다.

## 주행은 Navigator 인터페이스 뒤에 있다

`navigator` 파라미터가 고른다 — `nav2` 는 `NavigateToPose` 로 실제 주행하고,
기본값 `null` 은 목표를 `~/goal` 로 발행하고 로그만 남긴다. **기본이 null 인 것은
이 노드가 launch 밖에서 켜졌을 때 모터를 돌리지 않게 하려는 것**이며, 「아직
구현되지 않았다」는 뜻이 아니다(S15P11A301-172 에서 연결됐다. 종전 이 문단은
「235 가 꽂으면」이라고 적혀 있었고 그것은 낡은 서술이었다). 축소판(순찰 시퀀스)도
같은 자리(`select_goal`)에서 갈아끼운다.

## 상태 게이트

`/mission/status` 의 `movementAllowed` 만 본다. 상태 판단을 여기서 다시 하지
않는다(26.2) — PAUSED·ESTOP·MANUAL 에서 멈추는 근거를 두 곳에 두면 언젠가
한쪽만 바뀐다. 상태 토픽이 `status_stale_s` 동안 없으면 **멈춘다.** 마지막
값을 믿고 계속 달리면 mission_manager 가 죽었을 때 아무도 로봇을 세우지 않는다.

`status_stale_s` 3초는 `mission_manager` 의 **1Hz heartbeat 를 세 번 놓친 것**이다
(S15P11A301-320). 그 heartbeat 가 없던 동안에는 전이가 없는 정상 주행에서 3초마다
HOLD 로 떨어져 목표 선택이 6·20·8초 간격으로 들쭉날쭉했다 — 두 값은 한 쌍이므로
한쪽을 바꾸면 다른 쪽을 함께 본다.
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

from .blacklist import GoalBlacklist
from .coverage import CameraCoverage, observation_candidates
from .frontier import cluster_alive, extract_frontiers
from .grid import GridInfo
from .navigator import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    Nav2Navigator,
    NullNavigator,
)
from .selector import Candidate, Commitment, Weights, compute_gains, score
from .sweep import needed_sectors, plan_sweep


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
        # 주행 층 선택 (S15P11A301-172). 'nav2' 는 NavigateToPose 로 실제 주행하고
        # 'null' 은 목표만 로그로 남긴다. 기본을 null 로 두는 이유는 이 노드가
        # enable_exploration 없이 켜지는 구성에서 모터를 돌리지 않게 하는 것이다 —
        # launch 가 명시적으로 nav2 를 준다.
        self.declare_parameter('navigator', 'null')
        self.declare_parameter('nav2_action_name', 'navigate_to_pose')
        # 도달 실패 상한. 넘으면 그 자리를 임무 동안 후보에서 뺀다.
        self.declare_parameter('blacklist_after_failures', 3)
        self.declare_parameter('blacklist_radius_m', 0.5)

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

        self._blacklist = GoalBlacklist(
            radius_m=float(self.get_parameter('blacklist_radius_m').value),
            max_failures=int(self.get_parameter('blacklist_after_failures').value),
        )

        kind = str(self.get_parameter('navigator').value).lower()
        if kind == 'nav2':
            self._navigator = Nav2Navigator(
                self, str(self.get_parameter('nav2_action_name').value)
            )
            self.get_logger().info('navigator=nav2 — NavigateToPose 로 실제 주행한다')
        else:
            self._navigator = NullNavigator(self)
            self.get_logger().info('navigator=null — 목표만 발행하고 주행하지 않는다')

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

        self._consume_outcome()

        if self._grid is None or self._info is None:
            self._publish_status('WAIT_MAP')
            return
        pose = self._pose()
        if pose is None:
            return
        x, y, yaw = pose
        home_x, home_y = self._home if self._home else (x, y)

        # 소스 A: frontier
        clusters = extract_frontiers(
            self._grid, self._info,
            min_cells=self.get_parameter('min_frontier_cells').value,
            home_x=home_x, home_y=home_y,
            max_radius_m=self.get_parameter('max_radius_m').value,
        )
        candidates = self._drop_blacklisted([
            Candidate(x=c.rep_x, y=c.rep_y, kind='frontier', payload=c) for c in clusters
        ])

        # 소스 B: 관측 후보. frontier 소진은 지도 완성이지 수색 완료가 아니다.
        unseen = self._coverage.unseen_free(self._grid, self._info)
        if not candidates:
            candidates = self._drop_blacklisted([
                Candidate(x=ox, y=oy, kind='observation')
                for ox, oy in observation_candidates(unseen)
            ])

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
            key=lambda c: score(c, self._weights, from_x=x, from_y=y, from_yaw=yaw,
                                history=self._visit_history),
        )
        best_score = score(best, self._weights, from_x=x, from_y=y, from_yaw=yaw,
                           history=self._visit_history)
        now = self._now()

        if self._commitment is not None:
            committed = self._commitment.candidate
            vanished = (
                committed.kind == 'frontier'
                and committed.payload is not None
                and not cluster_alive(self._grid, committed.payload)
            )
            # 소멸 판정에도 최소 나이를 건다 (S15P11A301-360). 지도가 2초마다
            # 갱신되며 군집이 일시적으로 소멸 판정을 받는데, 이것이 약속의
            # min_age(5초)를 우회해 실기동에서 목표가 2~4초마다 갈렸다 —
            # 「좌우 왔다갔다」의 한 축. 갓 약속한 목표는 지도 노이즈 한 번으로
            # 버리지 않는다. 진짜 소멸이면 3초 뒤에도 소멸이다.
            vanished = vanished and (now - self._commitment.committed_at) >= 3.0
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

    def _consume_outcome(self) -> None:
        """끝난 목표의 결말을 반영한다 (S15P11A301-172).

        약속을 여기서 푸는 것이 요점이다. 풀지 않으면 도달한 자리에 계속 약속이
        남아, 새 후보가 125% 를 넘길 때까지 로봇이 서 있는다.
        """
        outcome = self._navigator.take_outcome()
        if outcome is None:
            return
        if outcome.status == STATUS_CANCELED:
            # 우리가 취소한 것이다(게이트 닫힘·사람 발견). 이미 약속을 풀었다.
            return
        if outcome.status == STATUS_SUCCEEDED:
            self._blacklist.record_success(outcome.x, outcome.y)
        elif outcome.status == STATUS_FAILED:
            count = self._blacklist.record_failure(outcome.x, outcome.y)
            limit = int(self.get_parameter('blacklist_after_failures').value)
            if count >= limit:
                self.get_logger().warn(
                    f'({outcome.x:.2f}, {outcome.y:.2f}) 도달 실패 {count}회 — '
                    '임무 동안 후보에서 제외한다'
                )
        # UNAVAILABLE 은 실패로 세지 않는다. Nav2 활성 전의 정상 상황이다.
        self._commitment = None

    def _drop_blacklisted(self, candidates: list[Candidate]) -> list[Candidate]:
        """도달 실패가 상한에 닿은 자리를 뺀다.

        빼지 않으면 지도가 그대로인 동안 같은 후보가 계속 1위여서, 거부당하는
        목표로 2초마다 다시 보낸다 — 겉보기 증상은 "탐사가 도는데 제자리"다.
        """
        return [c for c in candidates if not self._blacklist.is_blocked(c.x, c.y)]

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
            # 제외된 자리 수. 0 이 아닌데 로봇이 안 움직이면 도달 실패가 쌓인
            # 것이고, DONE 인데 이 값이 크면 「다 봤다」가 아니라 「못 갔다」다.
            'blockedGoals': self._blacklist.blocked_count,
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

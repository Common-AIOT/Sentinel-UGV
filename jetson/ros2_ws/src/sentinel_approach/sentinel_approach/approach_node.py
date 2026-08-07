#!/usr/bin/env python3
"""접근 노드 — ROS 껍데기 (S15P11A301-247).

판단은 전부 `approach.py` 에 있고 여기는 구독·발행·시계만 다룬다. 그래서 이 파일에는
시험이 없다 — `sentinel_exploration` 과 같은 규칙이다.

## 입력과 출력

    /perception/person_candidates   사람 후보 (25.2). bbox 로 방위각을 만든다
    /scan                           그 방위의 거리
    /mission/status                 state·movementAllowed·speedLimit
      →
    /cmd_vel_nav                    안전 체인의 자율 입력 (24.1). 직접 모터로 가지 않는다
    /mission/signal                 SAFE_POSE_REACHED · APPROACH_FAILED

`/cmd_vel_nav` 로 내는 이유는 24.1 이 **모든 자율 명령은 체인을 거친다**고 정했기
때문이다. Nav2 와 같은 토픽이지만 겹치지 않는다 — `PERSON_APPROACHING` 에서
exploration 은 HOLD 이고 Nav2 에는 목표가 없다.

## 상태 게이트

`PERSON_APPROACHING` 이면서 `movementAllowed` 일 때만 명령을 낸다. 상태 판단을 여기서
다시 하지 않는다(26.2) — 그 근거를 두 곳에 두면 언젠가 한쪽만 바뀐다. 상태가
`status_stale_s` 동안 없으면 멈춘다(`exploration_node` 와 같은 이유이고, 그 값은
`mission_manager` 의 1Hz heartbeat 를 세 번 놓친 것이다 — S15P11A301-320).

## 사람을 놓치면 멈춘다

후보가 `person_lost_s` 동안 비면 **마지막 방위로 계속 가지 않는다.** 사람이 옆으로
비켰는데 그 자리를 향해 계속 전진하면 그것이 사고다. 멈추고 `APPROACH_FAILED` 를 낸다 —
30.3 이 「접근 경로를 만들 수 없으면 현재 위치에서 상호작용」이라고 정했으므로, 그 판단은
`mission_manager` 가 한다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

from .approach import (
    DEFAULT_MAX_SPEED_MPS,
    DEFAULT_MIN_TURNING_RADIUS_M,
    DEFAULT_STOP_DISTANCE_M,
    STOPPED,
    ApproachLimits,
    bearing_from_box,
    plan_approach,
    range_at_bearing,
)

_APPROACHING = 'PERSON_APPROACHING'


class ApproachNode(Node):
    def __init__(self) -> None:
        super().__init__('approach')

        self.declare_parameter('candidates_topic', '/perception/person_candidates')
        self.declare_parameter('scan_topic', '/scan')
        self.declare_parameter('status_topic', '/mission/status')
        self.declare_parameter('signal_topic', '/mission/signal')
        self.declare_parameter('cmd_vel_topic', '/cmd_vel_nav')
        # BRIO 100 대각 58° 의 수평 환산. exploration_node 와 **같은 값**이어야 한다 —
        # 두 노드가 같은 카메라로 다른 화각을 믿으면 목표와 접근이 어긋난다.
        self.declare_parameter('camera_hfov_deg', 52.0)
        self.declare_parameter('image_width_px', 1280)
        self.declare_parameter('max_speed_mps', DEFAULT_MAX_SPEED_MPS)
        self.declare_parameter('min_turning_radius_m', DEFAULT_MIN_TURNING_RADIUS_M)
        self.declare_parameter('stop_distance_m', DEFAULT_STOP_DISTANCE_M)
        self.declare_parameter('scan_window_beams', 2)
        self.declare_parameter('control_period_s', 0.1)
        self.declare_parameter('status_stale_s', 3.0)
        self.declare_parameter('person_lost_s', 1.5)

        self._limits = ApproachLimits(
            max_speed_mps=float(self.get_parameter('max_speed_mps').value),
            min_turning_radius_m=float(
                self.get_parameter('min_turning_radius_m').value
            ),
            stop_distance_m=float(self.get_parameter('stop_distance_m').value),
        )

        self._bearing: float | None = None
        self._bearing_at: float | None = None
        self._scan: LaserScan | None = None
        self._state: str | None = None
        self._movement_allowed = False
        self._speed_limit: float | None = None
        self._status_at: float | None = None
        # 도착·실패를 한 번만 보낸다. 신호가 반복되면 상태 머신이 같은 전이를 여러 번
        # 무시하고 로그만 지저분해진다.
        self._reported = False

        latched = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        best_effort = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=5,
        )

        self.create_subscription(
            String, self._param('candidates_topic'), self._on_candidates, best_effort
        )
        self.create_subscription(
            LaserScan, self._param('scan_topic'), self._on_scan, best_effort
        )
        self.create_subscription(
            String, self._param('status_topic'), self._on_status, latched
        )
        self._cmd_pub = self.create_publisher(Twist, self._param('cmd_vel_topic'), 10)
        # 신호를 잃으면 상태 머신이 PERSON_APPROACHING 에 머문다. RELIABLE 이어야 한다.
        self._signal_pub = self.create_publisher(
            String,
            self._param('signal_topic'),
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10,
            ),
        )

        self.create_timer(
            float(self.get_parameter('control_period_s').value), self._tick
        )
        self.get_logger().info(
            f'approach 시작 (bearing-only). 상한 {self._limits.max_speed_mps:.2f}m/s, '
            f'정지거리 {self._limits.stop_distance_m:.2f}m, '
            f'R_min {self._limits.min_turning_radius_m:.2f}m'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ── 입력 ────────────────────────────────────────────────────────────────

    def _on_candidates(self, message: String) -> None:
        """가장 가까워 보이는 후보 하나의 방위를 기억한다.

        여러 명이면 **bbox 가 가장 큰 쪽**을 고른다. 거리를 아직 모르므로 크기가
        유일한 근사이고, 32-6 이 「동시 발견은 encounter 하나」로 정했으므로 누구에게
        가든 이벤트는 같다.
        """
        try:
            body = json.loads(message.data)
            candidates = body.get('candidates') or []
        except (ValueError, TypeError):
            return
        boxed = [c for c in candidates if isinstance(c.get('box'), dict)]
        if not boxed:
            return
        best = max(boxed, key=lambda c: float(c['box'].get('height') or 0.0))
        box = best['box']
        try:
            center_x = float(box['x']) + float(box['width']) / 2.0
        except (KeyError, TypeError, ValueError):
            return
        self._bearing = bearing_from_box(
            center_x,
            float(self._param('image_width_px')),
            math.radians(float(self._param('camera_hfov_deg'))),
        )
        self._bearing_at = self._now()

    def _on_scan(self, message: LaserScan) -> None:
        self._scan = message

    def _on_status(self, message: String) -> None:
        try:
            body = json.loads(message.data)
        except (ValueError, TypeError):
            return
        state = body.get('state')
        self._state = state if isinstance(state, str) else None
        allowed = body.get('movementAllowed')
        self._movement_allowed = allowed if isinstance(allowed, bool) else False
        limit = body.get('speedLimit')
        self._speed_limit = float(limit) if isinstance(limit, (int, float)) else None
        self._status_at = self._now()
        if self._state != _APPROACHING:
            # 상태를 벗어나면 다음 접근을 위해 보고 플래그를 푼다.
            self._reported = False

    # ── 주기 ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if not self._gate_open():
            self._publish(STOPPED)
            return

        now = self._now()
        lost = (
            self._bearing_at is None
            or now - self._bearing_at > float(self._param('person_lost_s'))
        )
        if lost:
            self._publish(STOPPED)
            self._report('APPROACH_FAILED', '사람을 놓쳤다 — 마지막 방위로 계속 가지 않는다')
            return

        command = plan_approach(
            bearing_rad=self._bearing,
            distance_m=self._distance(),
            limits=self._limits,
            speed_limit_mps=self._speed_limit,
        )
        self._publish(command)
        if command.arrived:
            self._report('SAFE_POSE_REACHED', '안전 관측 거리에 도달했다')

    def _gate_open(self) -> bool:
        if self._state != _APPROACHING or not self._movement_allowed:
            return False
        if self._status_at is None:
            return False
        return self._now() - self._status_at <= float(self._param('status_stale_s'))

    def _distance(self) -> float | None:
        scan = self._scan
        if scan is None or self._bearing is None:
            return None
        return range_at_bearing(
            list(scan.ranges),
            scan.angle_min,
            scan.angle_increment,
            self._bearing,
            window=int(self._param('scan_window_beams')),
            range_min=scan.range_min,
            range_max=scan.range_max,
        )

    # ── 출력 ────────────────────────────────────────────────────────────────

    def _publish(self, command) -> None:
        message = Twist()
        message.linear.x = command.linear_mps
        message.angular.z = command.angular_radps
        self._cmd_pub.publish(message)

    def _report(self, signal: str, detail: str) -> None:
        """`mission-signal.schema.json` 형식으로 낸다.

        `sentAt` 이 필수이고 `additionalProperties: false` 다 — 스키마에 없는 키를
        넣으면 계약 위반이며, 사유 필드의 이름은 `detail` 이다.
        """
        if self._reported:
            return
        self._reported = True
        body = {
            'signal': signal,
            'sentAt': datetime.now(timezone.utc)
            .isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z'),
            'source': 'NAVIGATION',
            'encounterId': None,
            'detail': detail,
            'commandId': None,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self._signal_pub.publish(message)
        self.get_logger().info(f'{signal}: {detail}')


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ApproachNode()
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

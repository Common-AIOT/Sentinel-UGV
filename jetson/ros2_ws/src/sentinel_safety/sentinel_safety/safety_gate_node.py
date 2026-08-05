"""safety_gate — 체인의 마지막 관문 (S15P11A301-237).

    /cmd_vel_safe (collision_monitor)  →  [게이트]  →  /cmd_vel  →  vehicle_kinematics

판정은 `gate.py` 가 하고 이 파일은 ROS 배선만 한다.

## `/cmd_vel` 의 발행자는 이 노드 하나여야 한다

`vehicle_kinematics`(S15P11A301-234)가 `/cmd_vel` 을 구독해 모터로 보낸다. 즉
`/cmd_vel` 이 **바퀴 명령**이고, 여기 끼어드는 발행자가 생기면 안전 체인 전체를
우회한다. 기동 시 발행자 수를 세어 1 이 아니면 경고한다 — 우회는 조용히 일어나고
증상은 "왜 안 멈추지" 뿐이다.

## 왜 타이머로 내는가

들어온 명령에 반응해서만 발행하면, 상위가 끊겼을 때 **아무것도 발행하지 않는
상태**가 된다. 그러면 하류의 300ms 타임아웃이 걸려 정지하기는 하지만 "막았다" 와
"죽었다" 가 구별되지 않는다. 고정 주기로 돌면서 막을 때도 0 을 내면, 발행 주기가
게이트의 생존 신호가 된다.

주기는 명령 TTL(300ms)의 1/6 인 20Hz 다. TTL 보다 촘촘해야 TTL 만료를 제때
반영하고, 너무 촘촘하면 이미 포화된 CPU 를 더 먹는다(S15P11A301-249).
"""

from __future__ import annotations

import json

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Bool, String

from sentinel_safety.gate import GateInputs, GateTimeouts, evaluate

PUBLISH_HZ = 20.0


class SafetyGateNode(Node):

    def __init__(self) -> None:
        super().__init__('safety_gate')
        self.declare_parameter('command_ttl_s', 0.3)
        self.declare_parameter('mission_ttl_s', 10.0)
        self.declare_parameter('proximity_ttl_s', 1.0)
        self.declare_parameter('scan_ttl_s', 0.5)
        self.declare_parameter('input_topic', '/cmd_vel_safe')
        self.declare_parameter('output_topic', '/cmd_vel')

        self._command: tuple[float, float] | None = None
        self._command_stamp: float | None = None
        self._mission_state: str | None = None
        self._movement_allowed: bool | None = None
        self._mission_stamp: float | None = None
        self._protective_stop: bool | None = None
        self._protective_stamp: float | None = None
        self._scan_stamp: float | None = None
        # 직전 판정의 이유. 같은 이유를 20Hz 로 로그에 쏟지 않기 위해 비교용으로 든다.
        self._last_reasons: tuple[str, ...] | None = None

        output_topic = self.get_parameter('output_topic').value
        self._pub = self.create_publisher(Twist, output_topic, 10)
        self._state_pub = self.create_publisher(String, '/safety/gate_state', 10)

        self.create_subscription(
            Twist, self.get_parameter('input_topic').value, self._on_command, 10
        )
        # 임무 상태는 latched 다 — 상태가 바뀔 때만 발행되므로 volatile 로 구독하면
        # 늦게 뜬 이 노드가 현재 상태를 영원히 못 받는다(/map 과 같은 함정).
        self.create_subscription(
            String, '/mission/status', self._on_mission,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.create_subscription(
            Bool, '/proximity/protective_stop', self._on_protective, 10
        )
        # /scan 은 내용을 보지 않는다 — 거리 판정은 collision_monitor 의 몫이고
        # 여기서는 **오고 있는지만** 본다. best_effort 센서 QoS 로 받아야 한다.
        self.create_subscription(
            LaserScan, '/scan', self._on_scan, qos_profile_sensor_data
        )

        self.create_timer(1.0 / PUBLISH_HZ, self._tick)
        self.create_timer(5.0, self._check_sole_publisher)
        self.get_logger().info(
            f'safety_gate 시작: {self.get_parameter("input_topic").value} → {output_topic}'
        )

    # ── 시계 ────────────────────────────────────────────────────────────────
    def _now(self) -> float:
        # 단조 시계를 쓴다. 시스템 시각이 NTP 로 뒤로 점프하면 TTL 판정이
        # 뒤집혀 낡은 명령이 신선해진다.
        return self.get_clock().now().nanoseconds / 1e9

    # ── 구독 ────────────────────────────────────────────────────────────────
    def _on_command(self, message: Twist) -> None:
        self._command = (message.linear.x, message.angular.z)
        self._command_stamp = self._now()

    def _on_mission(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (json.JSONDecodeError, TypeError):
            # 파싱 실패를 삼키지 않는다. 상태를 갱신하지 않으면 TTL 이 만료돼
            # MISSION_STALE 로 막히므로, 안전한 쪽으로 떨어진다.
            self.get_logger().warn('임무 상태 JSON 파싱 실패 — 상태를 갱신하지 않는다')
            return
        state = payload.get('state')
        self._mission_state = state if isinstance(state, str) else None
        allowed = payload.get('movementAllowed')
        self._movement_allowed = allowed if isinstance(allowed, bool) else None
        self._mission_stamp = self._now()

    def _on_protective(self, message: Bool) -> None:
        self._protective_stop = message.data
        self._protective_stamp = self._now()

    def _on_scan(self, _message: LaserScan) -> None:
        self._scan_stamp = self._now()

    # ── 주기 ────────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        now = self._now()
        linear, angular = self._command if self._command else (0.0, 0.0)
        decision = evaluate(
            GateInputs(
                now_s=now,
                linear_mps=linear,
                angular_radps=angular,
                command_stamp_s=self._command_stamp,
                mission_state=self._mission_state,
                mission_stamp_s=self._mission_stamp,
                movement_allowed=self._movement_allowed,
                protective_stop=self._protective_stop,
                protective_stamp_s=self._protective_stamp,
                scan_stamp_s=self._scan_stamp,
            ),
            GateTimeouts(
                command_ttl_s=self.get_parameter('command_ttl_s').value,
                mission_ttl_s=self.get_parameter('mission_ttl_s').value,
                proximity_ttl_s=self.get_parameter('proximity_ttl_s').value,
                scan_ttl_s=self.get_parameter('scan_ttl_s').value,
            ),
        )

        command = Twist()
        command.linear.x = decision.linear_mps
        command.angular.z = decision.angular_radps
        self._pub.publish(command)

        # 이유가 **바뀔 때만** 찍는다. 20Hz 로 같은 줄을 쏟으면 로그가 다른 원인을
        # 덮고, 그때 진짜 원인을 찾는 데 시간이 든다.
        if decision.reasons != self._last_reasons:
            if decision.reasons:
                self.get_logger().warn('주행 차단: ' + ' | '.join(decision.reasons))
            elif self._last_reasons is not None:
                self.get_logger().info('주행 차단 해제 — 명령을 통과시킨다')
            self._last_reasons = decision.reasons

        state = String()
        state.data = json.dumps(
            {
                'blocked': decision.blocked,
                'reasons': list(decision.reasons),
                'linear': decision.linear_mps,
                'angular': decision.angular_radps,
            },
            ensure_ascii=False,
        )
        self._state_pub.publish(state)

    def _check_sole_publisher(self) -> None:
        """`/cmd_vel` 에 다른 발행자가 끼었는지 본다 (우회 감시)."""
        topic = self.get_parameter('output_topic').value
        count = self.count_publishers(topic)
        if count > 1:
            self.get_logger().error(
                f'{topic} 발행자가 {count} 개다. 안전 체인을 우회하는 발행자가 있다 — '
                'ros2 topic info 로 찾아 제거하라. 이 상태에서는 게이트가 0 을 내도 '
                '다른 발행자의 속도가 모터로 간다.'
            )


def main() -> None:
    rclpy.init()
    node = SafetyGateNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # 종료 직전에 0 을 한 번 낸다. 마지막 값이 latch 되지는 않지만,
        # 하류가 그것을 받으면 TTL 을 기다리지 않고 즉시 멈춘다.
        try:
            node._pub.publish(Twist())
        except Exception:  # noqa: BLE001 - 종료 경로에서 무엇이든 삼킨다
            pass
        node.destroy_node()
        # try_shutdown 을 쓴다. SIGTERM 으로 이미 context 가 내려간 뒤
        # shutdown() 을 부르면 예외가 난다(S15P11A301-234 에서 겪었다).
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()

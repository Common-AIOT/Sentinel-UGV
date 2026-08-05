"""command_mux — 자율/수동 중재 (S15P11A301-237).

    /cmd_vel_nav    ─┐
                     ├→ [controlMode 로 선택] → /cmd_vel_muxed → velocity_smoother
    /cmd_vel_manual ─┘

선택은 `mux.py` 가 하고 이 파일은 ROS 배선만 한다. TTL 판정은 여기서 한다 —
`mux.py` 에 시계를 넣지 않기 위해서다(시계가 두 곳에 있으면 시험이 시계에 묶인다).

## 왜 여기서도 0 을 계속 내는가

`safety_gate` 와 같은 이유다. 통과시킬 것이 없을 때 발행을 멈추면 하류
(`velocity_smoother`)가 입력 없음으로 자기 판단을 하게 되고, "모드가 안 맞아서
막았다" 와 "mux 가 죽었다" 가 구별되지 않는다.
"""

from __future__ import annotations

import json

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from sentinel_safety.mux import select

PUBLISH_HZ = 20.0


class CommandMuxNode(Node):

    def __init__(self) -> None:
        super().__init__('command_mux')
        self.declare_parameter('source_ttl_s', 0.3)
        self.declare_parameter('output_topic', '/cmd_vel_muxed')

        self._auto: tuple[float, float] | None = None
        self._auto_stamp: float | None = None
        self._manual: tuple[float, float] | None = None
        self._manual_stamp: float | None = None
        self._control_mode: str | None = None
        self._last_note: str | None = None

        self._pub = self.create_publisher(Twist, self.get_parameter('output_topic').value, 10)

        self.create_subscription(Twist, '/cmd_vel_nav', self._on_auto, 10)
        self.create_subscription(Twist, '/cmd_vel_manual', self._on_manual, 10)
        # 임무 상태는 latched — safety_gate 와 같은 이유로 transient_local 로 받는다.
        self.create_subscription(
            String, '/mission/status', self._on_mission,
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )

        self.create_timer(1.0 / PUBLISH_HZ, self._tick)
        self.get_logger().info(
            'command_mux 시작: /cmd_vel_nav·/cmd_vel_manual → '
            f'{self.get_parameter("output_topic").value}'
        )

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_auto(self, message: Twist) -> None:
        self._auto = (message.linear.x, message.angular.z)
        self._auto_stamp = self._now()

    def _on_manual(self, message: Twist) -> None:
        self._manual = (message.linear.x, message.angular.z)
        self._manual_stamp = self._now()

    def _on_mission(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warn('임무 상태 JSON 파싱 실패 — controlMode 를 갱신하지 않는다')
            return
        mode = payload.get('controlMode')
        self._control_mode = mode if isinstance(mode, str) else None

    def _fresh(self, value, stamp: float | None):
        """TTL 이 지난 소스는 없는 것으로 다룬다."""
        if value is None or stamp is None:
            return None
        if (self._now() - stamp) > self.get_parameter('source_ttl_s').value:
            return None
        return value

    def _tick(self) -> None:
        decision = select(
            self._control_mode,
            auto=self._fresh(self._auto, self._auto_stamp),
            manual=self._fresh(self._manual, self._manual_stamp),
        )

        command = Twist()
        command.linear.x = decision.linear_mps
        command.angular.z = decision.angular_radps
        self._pub.publish(command)

        note = decision.reason if not decision.passed else f'통과: {decision.source}'
        if note != self._last_note:
            if decision.passed:
                self.get_logger().info(note)
            else:
                self.get_logger().warn(f'명령 차단 — {note}')
            self._last_note = note


def main() -> None:
    rclpy.init()
    node = CommandMuxNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node._pub.publish(Twist())
        except Exception:  # noqa: BLE001
            pass
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()

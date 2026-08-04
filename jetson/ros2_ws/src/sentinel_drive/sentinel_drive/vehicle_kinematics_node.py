"""vehicle_kinematics — /cmd_vel 을 모터 브리지 명령으로 (S15P11A301-234).

자율주행 사슬의 마지막 ROS 노드다. 판단(역운동학·포화)은 `kinematics.py` 순수
모듈에 있고, 여기는 구독·발행·watchdog 타이머만 다룬다.

    /cmd_vel (Twist)  →  [역운동학·포화]  →  /esp32_motor_bridge/drive_command (JSON String)

## watchdog 이 ESP32 와 별개로 여기에도 있는 이유

ESP32 는 300ms 동안 유효 명령이 없으면 스스로 정지한다(34장). 그런데 그것은
**직렬 링크까지 살아 있을 때의 마지막 방어선**이고, 이 노드의 watchdog 은
상류(Nav2·탐사)가 죽었을 때 ROS 그래프 안에서 먼저 0 을 내는 첫 방어선이다.
층이 두 개인 것이 의도다 — 한 층이 다른 층의 실패를 덮는다.

정지는 상태 전이 때 **한 번** 보낸다. 계속 0 을 쏘면 나중에 command_mux(#237)가
붙었을 때 수동 채널과 0 이 경합한다.
"""

from __future__ import annotations

import json

import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String

from .kinematics import MODE_AUTO, drive_command, saturate, stop_command, wheel_speeds_mps


class VehicleKinematicsNode(Node):
    def __init__(self) -> None:
        super().__init__('vehicle_kinematics')

        # 트랙폭 잠정값. 실측(TBD-CAL-002, S15P11A301-248)이 오면 파라미터만
        # 바꾼다. 값이 틀리면 직진은 맞고 **회전 반경만 어긋난다** — 지령 ω 대비
        # 실제 yaw rate 의 비가 W 오차의 비다.
        self.declare_parameter('track_width_m', 0.30)
        # 바퀴 상한 잠정값. 보수적으로 수동 상한(24.2, 0.30m/s)에 맞춘다.
        # RS540 실측(TBD-CAL-001) 전까지 이 위로 못 나가게 막는 물리 한계 역할.
        self.declare_parameter('max_wheel_mmps', 300)
        self.declare_parameter('cmd_vel_timeout_s', 0.3)
        self.declare_parameter('watchdog_period_s', 0.1)
        # 명세 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2. 이 노드는 자율 사슬이다.
        self.declare_parameter('mode', MODE_AUTO)
        self.declare_parameter('drive_command_topic', '/esp32_motor_bridge/drive_command')

        self._pub = self.create_publisher(
            String, self.get_parameter('drive_command_topic').value, 10
        )
        self.create_subscription(Twist, '/cmd_vel', self._on_cmd_vel, 10)

        self._last_cmd_time: float | None = None
        self._stopped = True  # 시작 상태는 정지. 첫 cmd_vel 전에 아무것도 안 보낸다
        self.create_timer(self.get_parameter('watchdog_period_s').value, self._watchdog)

    def _on_cmd_vel(self, message: Twist) -> None:
        track_width = self.get_parameter('track_width_m').value
        max_wheel_mps = self.get_parameter('max_wheel_mmps').value / 1000.0

        left, right = wheel_speeds_mps(message.linear.x, message.angular.z, track_width)
        left, right = saturate(left, right, max_wheel_mps)
        payload = drive_command(left, right, mode=self.get_parameter('mode').value)

        self._publish(payload)
        self._last_cmd_time = self._now()
        self._stopped = False

    def _watchdog(self) -> None:
        """`/cmd_vel` 이 끊기면 정지 명령을 한 번 낸다."""
        if self._stopped or self._last_cmd_time is None:
            return
        if self._now() - self._last_cmd_time < self.get_parameter('cmd_vel_timeout_s').value:
            return
        self._publish(stop_command(mode=self.get_parameter('mode').value))
        self._stopped = True
        self.get_logger().warn(
            'cmd_vel 이 끊겨 정지 명령을 보냈다. 상류(Nav2·탐사) 노드를 확인하라.'
        )

    def _publish(self, payload: dict) -> None:
        message = String()
        message.data = json.dumps(payload)
        self._pub.publish(message)

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9


def main() -> None:
    rclpy.init()
    node = VehicleKinematicsNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # SIGINT 는 KeyboardInterrupt, SIGTERM 은 rclpy 시그널 핸들러가 컨텍스트를
        # 내리며 ExternalShutdownException 을 던진다. 후자를 안 잡으면 systemd·
        # demo_down 의 정상 종료가 traceback + 비정상 종료 코드로 남는다 —
        # Nav2 실기동 스모크(S15P11A301-235)에서 실제로 그랬다.
        pass
    finally:
        # 마지막 명령과 무관하게 0 을 보내고 나간다 (14.7 프로그램 종료 안전).
        # SIGTERM 경로에서는 컨텍스트가 이미 죽어 발행이 실패할 수 있는데, 그때는
        # ESP32 의 300ms watchdog 이 정지를 담보한다(층이 두 개인 이유).
        try:
            node._publish(stop_command(mode=node.get_parameter('mode').value))
        except Exception:  # noqa: BLE001 — 종료 경로에서는 실패해도 계속 내려간다
            pass
        node.destroy_node()
        # shutdown() 은 이미 내려간 컨텍스트에서 RCLError 를 던진다.
        # try_shutdown() 은 살아 있을 때만 내린다 — 이중 호출에 안전하다.
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()

"""vehicle_kinematics — /cmd_vel 을 모터 브리지 명령으로 (S15P11A301-234·297).

자율주행 사슬의 마지막 ROS 노드다. 판단(전륜 조향 역운동학·포화·거부)은
`kinematics.py` 순수 모듈에 있고, 여기는 구독·발행·watchdog·진단만 다룬다.

    /cmd_vel (Twist)  →  [자전거 모델 변환]  →  /esp32_motor_bridge/drive_command (JSON String)
                                                후륜 좌·우 mm/s + 조향각 mdeg

## watchdog 이 ESP32 와 별개로 여기에도 있는 이유

ESP32 는 300ms 동안 유효 명령이 없으면 스스로 정지한다(34장). 그런데 그것은
**직렬 링크까지 살아 있을 때의 마지막 방어선**이고, 이 노드의 watchdog 은
상류(Nav2·탐사)가 죽었을 때 ROS 그래프 안에서 먼저 0 을 내는 첫 방어선이다.
층이 두 개인 것이 의도다 — 한 층이 다른 층의 실패를 덮는다.

정지는 상태 전이 때 **한 번** 보낸다. 계속 0 을 쏘면 command_mux(#237)의 수동
채널과 0 이 경합한다. 그 정지 명령에도 **마지막 조향각을 실어 보낸다**(§34-7) —
0 을 보내면 관성 주행 중에 앞바퀴가 중립으로 돌아가 궤적이 바뀐다.

## 제자리 회전 거부를 진단으로 올린다

전륜 조향 차량은 `v≈0, ω≠0` 을 실행할 수 없다(§34-2). 상위가 그것을 계속
시도하면 로봇은 멈춘 채 아무 일도 일어나지 않으므로, 조용히 거부하지 않고
카운터와 `/diagnostics` 로 드러낸다. 정공법은 Nav2 쪽에서 rotation shim·spin 을
끄는 것이고(24.1) 이 거부는 마지막 방어선이다.
"""

from __future__ import annotations

import json
import math

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from std_msgs.msg import String

from .kinematics import (
    MODE_AUTO,
    REJECT_SPIN_IN_PLACE,
    VehicleLimits,
    drive_command,
    min_turning_radius_m,
    mode_byte,
    solve,
    stop_command,
)


class VehicleKinematicsNode(Node):
    def __init__(self) -> None:
        super().__init__('vehicle_kinematics')

        # 휠베이스 **실측**(2026-08-07, S15P11A301-299 2단계). 틀리면 직진은 맞고
        # **선회 반경만 어긋난다** — 지령 ω 대비 실제 yaw rate 의 비가 L 오차의 비다.
        self.declare_parameter('wheelbase_m', 0.683)
        # 최대 조향각 **실측**(2026-08-07, 좌우 동일). R_min = L/tan(δ) = 1.69m 가
        # 여기서 나온다 — 04장 24.1 과 nav2.yaml 이 그 값을 근거로 쓴다.
        #
        # **모터 ESP32 의 STEERING_MAX_MDEG 와 같아야 한다.** 여기가 더 크면 펌웨어가
        # 조용히 클램프하고 STEERING_COMMAND_INVALID 만 올라온다(§34-9 bit 14).
        #
        # 맞추는 방향에 주의한다 — **펌웨어를 실측에 맞춘다.** 종전 주석은
        # 「STEERING_MAX_MDEG(30000)와 같아야 한다」였는데, 그대로 따르면 실측 22°를
        # 30°로 되돌리게 된다. 30°는 실측 전 가정값이었다(S15P11A301-341).
        self.declare_parameter('max_steering_deg', 22.0)
        # 후륜 상한 잠정값. 보수적으로 수동 상한(24.2, 0.30m/s)과 같은 값을 쓴다.
        # RS540 실측(TBD-CAL-001) 전까지 이 위로 못 나가게 막는 물리 한계 역할.
        #
        # **이 상한은 자율 체인만 제한한다** (S15P11A301-298). 모바일 수동 조종은
        # 폰 → 모터 ESP32 직결이라 이 노드를 지나지 않으므로, 같은 0.30m/s 를
        # 지키는 것은 펌웨어의 MANUAL_MAX_DRIVE_MMPS 다. 두 값이 우연히 같을 뿐
        # 한쪽을 바꾼다고 다른 쪽이 따라오지 않는다.
        self.declare_parameter('max_drive_mmps', 300)
        # v_min: 이 속도 미만에서는 회두 명령을 거부한다(§34-2). steering.cpp 의
        # STEERING_MIN_DRIVE_MMPS(30)와 같은 값이다.
        self.declare_parameter('min_linear_mmps', 30)
        # 조향 변화율 상한 잠정값(60°/s). 급조향으로 차체가 튀지 않는 최대값을
        # §35-4 「조향 튜닝」에서 실측해 확정한다. 0 을 보내면 서보가 자기 최대
        # 속도로 꺾으므로 여기서 0 을 기본값으로 두지 않는다.
        self.declare_parameter('max_steering_rate_mdps', 60000)
        self.declare_parameter('cmd_vel_timeout_s', 0.3)
        self.declare_parameter('watchdog_period_s', 0.1)
        self.declare_parameter('diagnostic_period_s', 1.0)
        # 명세 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2. `controlMode` 를 아직 못
        # 받았을 때 쓸 기본값이며, 받은 뒤에는 `mode_byte()` 가 결정한다.
        self.declare_parameter('mode', MODE_AUTO)
        self.declare_parameter('drive_command_topic', '/esp32_motor_bridge/drive_command')
        self.declare_parameter('mission_status_topic', '/mission/status')

        self._pub = self.create_publisher(
            String, self.get_parameter('drive_command_topic').value, 10
        )
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.create_subscription(Twist, '/cmd_vel', self._on_cmd_vel, 10)

        # `controlMode` 를 따라가기 위한 구독 (S15P11A301-298).
        #
        # 수동 래치를 쥔 보드에 대고 `mode=2`(AUTO) 를 20Hz 로 주장하면 초당 50회
        # 다툰다. `mode=1` 은 래치와 **합의**하는 값이라 보드가 아무것도 되돌릴
        # 필요가 없다. 스트림 자체는 멈추지 않는다 — 그것이 보드의
        # `lastValidDriveCommandMs` 를 갱신하는 유일한 것이고, 멈추면 300ms
        # 워치독이 수동 주행 중에 트립한다.
        #
        # TRANSIENT_LOCAL: `/mission/status` 는 상태가 바뀔 때만 발행되므로 늦게 뜬
        # 이 노드가 다음 전이까지 모드를 모르면 안 된다(`command_mux_node` 와 같은
        # 근거).
        self._control_mode: str | None = None
        self.create_subscription(
            String,
            self.get_parameter('mission_status_topic').value,
            self._on_mission_status,
            QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )

        self._last_cmd_time: float | None = None
        self._stopped = True  # 시작 상태는 정지. 첫 cmd_vel 전에 아무것도 안 보낸다
        # 마지막으로 지령한 조향각. 정지·거부 경로에서 이 값을 유지한다(§34-7).
        # 부팅 직후에는 펌웨어도 중립에서 시작하므로 0 이 맞다(§34-6).
        self._steering_rad = 0.0
        self._spin_reject_count = 0
        self._steering_clamp_count = 0

        limits = self._limits()
        self.get_logger().info(
            f'전륜 조향 운동학: L={limits.wheelbase_m:.3f}m '
            f'δ_max={math.degrees(limits.max_steering_rad):.1f}° '
            f'R_min={min_turning_radius_m(limits):.3f}m '
            f'v_max={limits.max_drive_mps:.3f}m/s v_min={limits.min_linear_mps:.3f}m/s '
            '— 제자리 회전은 실행할 수 없다(§34-2)'
        )

        self.create_timer(self.get_parameter('watchdog_period_s').value, self._watchdog)
        self.create_timer(
            self.get_parameter('diagnostic_period_s').value, self._publish_diagnostics
        )

    def _limits(self) -> VehicleLimits:
        return VehicleLimits(
            wheelbase_m=float(self.get_parameter('wheelbase_m').value),
            max_steering_rad=math.radians(float(self.get_parameter('max_steering_deg').value)),
            max_drive_mps=self.get_parameter('max_drive_mmps').value / 1000.0,
            min_linear_mps=self.get_parameter('min_linear_mmps').value / 1000.0,
        )

    def _on_mission_status(self, message: String) -> None:
        """`controlMode` 하나만 읽는다. 판단하지 않고 따라간다."""
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'mission_status JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict):
            return
        raw = payload.get('controlMode')
        mode = raw if isinstance(raw, str) else None
        if mode == self._control_mode:
            return
        previous = self._control_mode
        self._control_mode = mode
        self.get_logger().info(
            f'controlMode {previous} → {mode}. '
            f'DRIVE_COMMAND.mode 를 {self._mode_byte()} 로 보낸다'
        )

    def _mode_byte(self) -> int:
        return mode_byte(
            self._control_mode, default=int(self.get_parameter('mode').value)
        )

    def _on_cmd_vel(self, message: Twist) -> None:
        solution = solve(
            message.linear.x,
            message.angular.z,
            self._limits(),
            hold_steering_rad=self._steering_rad,
        )

        if not solution.accepted:
            self._spin_reject_count += 1
            if solution.reject_reason == REJECT_SPIN_IN_PLACE:
                self.get_logger().warn(
                    f'v≈0 에서 ω={message.angular.z:.3f}rad/s 명령을 거부했다 — '
                    '전륜 조향 차량은 제자리 회전을 할 수 없다(§34-2). '
                    'Nav2 rotation shim·spin 복구가 켜져 있는지 확인하라',
                    throttle_duration_sec=5.0,
                )
            # 거부는 "아무것도 하지 않는다"가 아니라 "구동 0, 조향 유지"다.
            # 정지 명령을 한 번 내고 watchdog 이 다시 쏘지 않게 _stopped 를 세운다.
            if not self._stopped:
                self._publish(self._stop_payload())
                self._stopped = True
            self._last_cmd_time = self._now()
            return

        if solution.steering_clamped:
            self._steering_clamp_count += 1

        self._steering_rad = solution.steering_rad
        self._publish(
            drive_command(
                solution.speed_mps,
                solution.steering_rad,
                mode=self._mode_byte(),
                max_steering_rate_mdps=self.get_parameter('max_steering_rate_mdps').value,
            )
        )
        self._last_cmd_time = self._now()
        self._stopped = False

    def _watchdog(self) -> None:
        """`/cmd_vel` 이 끊기면 정지 명령을 한 번 낸다."""
        if self._stopped or self._last_cmd_time is None:
            return
        if self._now() - self._last_cmd_time < self.get_parameter('cmd_vel_timeout_s').value:
            return
        self._publish(self._stop_payload())
        self._stopped = True
        self.get_logger().warn(
            'cmd_vel 이 끊겨 정지 명령을 보냈다. 상류(Nav2·탐사) 노드를 확인하라. '
            '조향각은 마지막 목표를 유지한다(§34-7)'
        )

    def _stop_payload(self) -> dict:
        return stop_command(
            steering_rad=self._steering_rad,
            mode=self._mode_byte(),
            max_steering_rate_mdps=self.get_parameter('max_steering_rate_mdps').value,
        )

    def _publish_diagnostics(self) -> None:
        limits = self._limits()
        level = DiagnosticStatus.WARN if self._spin_reject_count else DiagnosticStatus.OK
        status = DiagnosticStatus(
            level=level,
            name='sentinel_drive: vehicle_kinematics',
            message=(
                '제자리 회전 명령 거부 누적' if self._spin_reject_count else '정상'
            ),
            hardware_id='front_steering',
            values=[
                KeyValue(key='spin_in_place_reject_count', value=str(self._spin_reject_count)),
                KeyValue(key='steering_clamp_count', value=str(self._steering_clamp_count)),
                KeyValue(
                    key='steering_deg', value=f'{math.degrees(self._steering_rad):.2f}'
                ),
                KeyValue(key='wheelbase_m', value=f'{limits.wheelbase_m:.3f}'),
                KeyValue(
                    key='min_turning_radius_m', value=f'{min_turning_radius_m(limits):.3f}'
                ),
            ],
        )
        array = DiagnosticArray()
        array.header.stamp = self.get_clock().now().to_msg()
        array.status = [status]
        self._diagnostics_pub.publish(array)

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
        # 마지막 명령과 무관하게 구동 0 을 보내고 나간다 (14.7 프로그램 종료 안전).
        # 조향각은 여기서도 유지한다 — 종료가 곧 정차는 아니다(§34-7).
        # SIGTERM 경로에서는 컨텍스트가 이미 죽어 발행이 실패할 수 있는데, 그때는
        # ESP32 의 300ms watchdog 이 정지를 담보한다(층이 두 개인 이유).
        try:
            node._publish(node._stop_payload())
        except Exception:  # noqa: BLE001 — 종료 경로에서는 실패해도 계속 내려간다
            pass
        node.destroy_node()
        # shutdown() 은 이미 내려간 컨텍스트에서 RCLError 를 던진다.
        # try_shutdown() 은 살아 있을 때만 내린다 — 이중 호출에 안전하다.
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()

"""모터 ESP32 브리지 노드 (S15P11A301-84).

USB 직렬(921600bps, COBS+CRC16)로 모터 ESP32(`esp32_motor_comm` 스케치)와
통신한다. 범위는 통신 계층뿐이다 - 실제 BTS7960 PWM 제어·차량 기구학 변환은 이
노드가 아니라 아직 없는 `vehicle_kinematics_node`/`command_mux_node`(§34-11
`sentinel_control`)의 몫이다. 그 노드들이 없어 `~/drive_command`는 JSON
문자열(std_msgs/String)로 받는 placeholder다 - 전용 `esp32_bridge_msgs`
인터페이스 패키지는 이번 범위에서 만들지 않기로 확정했다(계획 §5).

시동 시 HELLO/HELLO_ACK 핸드셰이크로 버전·role을 확인한다(§34-6). 300ms 통신
워치독과 실제 정지는 ESP32 펌웨어가 로컬로 수행하므로(esp32_motor_comm의
control_task), 이 노드가 죽거나 재시작해도 모터 ESP32는 독립적으로 안전하게
정지한다 - 이 노드는 명령을 전달하고 상태를 보고할 뿐이다.
"""

from __future__ import annotations

import json
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import Trigger

from .diagnostics import RebootDetector, build_array, build_status
from .packet_codec import (
    CobsError,
    CrcError,
    DriveCommand,
    LengthError,
    UnknownMessageTypeError,
    VersionError,
    build_frame,
    pack_drive_command,
    parse_frame,
    unpack_command_ack,
    unpack_diagnostic,
    unpack_drive_state,
    unpack_hello_ack,
)
from .protocol_constants import (
    BOARD_ROLE_MOTOR,
    MSG_COMMAND_ACK,
    MSG_DIAGNOSTIC,
    MSG_DRIVE_COMMAND,
    MSG_DRIVE_STATE,
    MSG_ESTOP_COMMAND,
    MSG_HELLO,
    MSG_HELLO_ACK,
    MSG_STOP_COMMAND,
    PROTOCOL_VERSION,
)
from .serial_transport import SerialTransport

_MOTOR_STATE_NAMES = [
    "BOOT",
    "SAFE_IDLE",
    "READY",
    "MANUAL_ACTIVE",
    "AUTO_ACTIVE",
    "STOPPING",
    "ESTOP_LATCHED",
    "FAULT_LATCHED",
]

_FRAME_PARSE_ERRORS = (CobsError, LengthError, VersionError, UnknownMessageTypeError, CrcError)


def _state_name(value: int) -> str:
    if 0 <= value < len(_MOTOR_STATE_NAMES):
        return _MOTOR_STATE_NAMES[value]
    return f"UNKNOWN({value})"


class Esp32MotorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("esp32_motor_bridge")

        self.declare_parameter("port", "/dev/sentinel_mcu_motor")
        self.declare_parameter("baudrate", 921600)
        self.declare_parameter("handshake_retry_period_s", 1.0)

        port = self.get_parameter("port").value
        baudrate = self.get_parameter("baudrate").value

        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._reboot_detector = RebootDetector()
        self._handshake_ok = False

        self._transport = SerialTransport(port, baudrate, logger=self.get_logger())
        self._transport.open()

        self._drive_state_pub = self.create_publisher(String, "~/drive_state", 10)
        self._command_ack_pub = self.create_publisher(String, "~/command_ack", 10)
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self.create_subscription(String, "~/drive_command", self._on_drive_command, 10)
        self.create_service(Trigger, "~/stop", self._on_stop_service)
        self.create_service(Trigger, "~/estop", self._on_estop_service)

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        retry_period = self.get_parameter("handshake_retry_period_s").value
        self._handshake_timer = self.create_timer(retry_period, self._handshake_timer_cb)
        self._send_hello()

    def destroy_node(self) -> bool:
        self._transport.close()
        return super().destroy_node()

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence = (self._sequence + 1) & 0xFFFF
            return self._sequence

    def _uptime_ms(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1_000_000) & 0xFFFFFFFF

    def _send_frame(self, message_type: int, payload: bytes = b"") -> None:
        frame = build_frame(message_type, self._next_sequence(), self._uptime_ms(), payload)
        try:
            self._transport.write_frame(frame)
        except Exception as exc:  # noqa: BLE001 - 포트가 아직 안 열렸을 수 있다
            self.get_logger().warn(f"프레임 전송 실패: {exc}")

    def _send_hello(self) -> None:
        self._send_frame(MSG_HELLO)

    def _handshake_timer_cb(self) -> None:
        if not self._handshake_ok:
            self._send_hello()

    def _on_drive_command(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
            cmd = DriveCommand(
                mode=int(data["mode"]),
                flags=int(data.get("flags", 0)),
                target_drive_left_mmps=int(data["target_drive_left_mmps"]),
                target_drive_right_mmps=int(data["target_drive_right_mmps"]),
                target_steering_mdeg=int(data.get("target_steering_mdeg", 0)),
                max_accel_mmps2=int(data.get("max_accel_mmps2", 0)),
                max_steering_rate_mdps=int(data.get("max_steering_rate_mdps", 0)),
                command_timeout_ms=int(data.get("command_timeout_ms", 300)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.get_logger().warn(f"drive_command 파싱 실패, 무시함: {exc}")
            return
        self._send_frame(MSG_DRIVE_COMMAND, pack_drive_command(cmd))

    def _on_stop_service(self, request, response):
        self._send_frame(MSG_STOP_COMMAND)
        response.success = True
        response.message = "STOP_COMMAND sent"
        return response

    def _on_estop_service(self, request, response):
        self._send_frame(MSG_ESTOP_COMMAND)
        response.success = True
        response.message = "ESTOP_COMMAND sent"
        return response

    def _rx_loop(self) -> None:
        while rclpy.ok():
            raw = self._transport.read_frame(timeout=0.5)
            if raw is None:
                continue
            try:
                frame = parse_frame(raw)
            except _FRAME_PARSE_ERRORS:
                continue  # 조용히 드롭 - 실행하지 않는다(§34-5)

            if self._reboot_detector.observe(frame.sender_uptime_ms):
                self.get_logger().warn("모터 ESP32 재부팅 감지, 재핸드셰이크")
                self._handshake_ok = False
                self._send_hello()

            self._dispatch(frame)

    def _dispatch(self, frame) -> None:
        if frame.message_type == MSG_HELLO_ACK:
            self._handle_hello_ack(frame.payload)
        elif frame.message_type == MSG_DRIVE_STATE:
            self._handle_drive_state(frame.payload)
        elif frame.message_type == MSG_COMMAND_ACK:
            self._handle_command_ack(frame.payload)
        elif frame.message_type == MSG_DIAGNOSTIC:
            self._handle_diagnostic(frame.payload)

    def _handle_hello_ack(self, payload: bytes) -> None:
        ack = unpack_hello_ack(payload)
        if ack.board_role != BOARD_ROLE_MOTOR:
            self.get_logger().error(f"포트에 연결된 보드가 모터가 아님(role={ack.board_role})")
            return
        if ack.protocol_version != PROTOCOL_VERSION:
            self.get_logger().error(
                f"프로토콜 버전 불일치: 보드={ack.protocol_version} Jetson={PROTOCOL_VERSION}"
            )
            return
        self._handshake_ok = True
        self.get_logger().info(
            f"모터 ESP32 핸드셰이크 완료: fw={ack.firmware_major}.{ack.firmware_minor}.{ack.firmware_patch} "
            f"state={_state_name(ack.board_state)}"
        )

    def _handle_drive_state(self, payload: bytes) -> None:
        state = unpack_drive_state(payload)
        msg = String()
        msg.data = json.dumps(
            {
                "applied_sequence": state.applied_sequence,
                "state": _state_name(state.state),
                "fault_flags": state.fault_flags,
                "drive_pwm_left_permille": state.drive_pwm_left_permille,
                "drive_pwm_right_permille": state.drive_pwm_right_permille,
                "target_steering_mdeg": state.target_steering_mdeg,
                "steering_actuator_cmd": state.steering_actuator_cmd,
                "estop_active": bool(state.estop_active),
                "driver_enabled": bool(state.driver_enabled),
            }
        )
        self._drive_state_pub.publish(msg)

    def _handle_command_ack(self, payload: bytes) -> None:
        ack = unpack_command_ack(payload)
        msg = String()
        msg.data = json.dumps(
            {
                "acked_message_type": ack.acked_message_type,
                "acked_sequence": ack.acked_sequence,
                "result": ack.result,
                "board_state": _state_name(ack.board_state),
            }
        )
        self._command_ack_pub.publish(msg)

    def _handle_diagnostic(self, payload: bytes) -> None:
        diag = unpack_diagnostic(payload)
        status = build_status(
            hardware_id=self.get_parameter("port").value,
            board_role=diag.board_role,
            board_state_name=_state_name(diag.board_state),
            fault_flags=diag.fault_flags,
            crc_error_count=diag.crc_error_count,
            dropped_frame_count=diag.dropped_frame_count,
            stale_sequence_count=diag.stale_sequence_count,
        )
        self._diagnostics_pub.publish(build_array(self.get_clock().now().to_msg(), [status]))


def main(args=None):
    rclpy.init(args=args)
    node = Esp32MotorBridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

"""센서 ESP32 브리지 노드 (S15P11A301-84).

USB 직렬(921600bps, COBS+CRC16)로 센서 ESP32(`esp32_sensor_comm` 스케치)와
통신한다. 범위는 통신 계층뿐이며, MT6701/DHT-11/HC-SR04 실측 로직은 아직
placeholder다(ESP32 쪽 sensor_task.cpp 참고).

프로토콜 메시지 표(§34-5)에는 Jetson→센서 보드로 가는 주기적 트래픽이
HELLO/CONFIG뿐이라, 센서 보드의 300ms 통신 워치독에 입력을 공급할 수단이
없다(계획 §8, 열린 리스크 2). 그래서 이 노드가 HELLO를 ~6-7Hz(150ms 간격,
워치독 타임아웃의 절반 이하)로 keep-alive처럼 재전송한다 - 이는 문서에 없던
gap-fill 결정이며 구현 후 §34-7에 addendum으로 반영해야 한다.
"""

from __future__ import annotations

import json
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray
from rclpy.node import Node
from std_msgs.msg import String

from .diagnostics import RebootDetector, build_array, build_status
from .packet_codec import (
    CobsError,
    CrcError,
    LengthError,
    UnknownMessageTypeError,
    VersionError,
    build_frame,
    parse_frame,
    unpack_diagnostic,
    unpack_encoder_state,
    unpack_environment_state,
    unpack_hello_ack,
    unpack_proximity_state,
)
from .protocol_constants import (
    BOARD_ROLE_SENSOR,
    MSG_DIAGNOSTIC,
    MSG_ENCODER_STATE,
    MSG_ENVIRONMENT_STATE,
    MSG_HELLO,
    MSG_HELLO_ACK,
    MSG_PROXIMITY_STATE,
    PROTOCOL_VERSION,
)
from .serial_transport import SerialTransport

_SENSOR_STATE_NAMES = ["BOOT", "STREAMING", "DEGRADED", "COMM_LOST"]

_FRAME_PARSE_ERRORS = (CobsError, LengthError, VersionError, UnknownMessageTypeError, CrcError)


def _state_name(value: int) -> str:
    if 0 <= value < len(_SENSOR_STATE_NAMES):
        return _SENSOR_STATE_NAMES[value]
    return f"UNKNOWN({value})"


class Esp32SensorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("esp32_sensor_bridge")

        self.declare_parameter("port", "/dev/sentinel_mcu_sensor")
        self.declare_parameter("baudrate", 921600)
        # 300ms 워치독의 절반 이하로 둬 한두 프레임 유실에도 트립되지 않게 한다.
        self.declare_parameter("keepalive_period_s", 0.15)

        port = self.get_parameter("port").value
        baudrate = self.get_parameter("baudrate").value

        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._reboot_detector = RebootDetector()

        self._transport = SerialTransport(port, baudrate, logger=self.get_logger())
        self._transport.open()

        self._encoder_pub = self.create_publisher(String, "~/encoder_state", 10)
        self._environment_pub = self.create_publisher(String, "~/environment_state", 10)
        self._proximity_pub = self.create_publisher(String, "~/proximity_state", 10)
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        keepalive_period = self.get_parameter("keepalive_period_s").value
        self._keepalive_timer = self.create_timer(keepalive_period, self._send_hello)

    def destroy_node(self) -> bool:
        self._transport.close()
        return super().destroy_node()

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence = (self._sequence + 1) & 0xFFFF
            return self._sequence

    def _uptime_ms(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1_000_000) & 0xFFFFFFFF

    def _send_hello(self) -> None:
        frame = build_frame(MSG_HELLO, self._next_sequence(), self._uptime_ms(), b"")
        try:
            self._transport.write_frame(frame)
        except Exception as exc:  # noqa: BLE001 - 포트가 아직 안 열렸을 수 있다
            self.get_logger().warn(f"HELLO 전송 실패: {exc}")

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
                self.get_logger().warn("센서 ESP32 재부팅 감지")

            self._dispatch(frame)

    def _dispatch(self, frame) -> None:
        if frame.message_type == MSG_HELLO_ACK:
            self._handle_hello_ack(frame.payload)
        elif frame.message_type == MSG_ENCODER_STATE:
            self._handle_encoder_state(frame.payload)
        elif frame.message_type == MSG_ENVIRONMENT_STATE:
            self._handle_environment_state(frame.payload)
        elif frame.message_type == MSG_PROXIMITY_STATE:
            self._handle_proximity_state(frame.payload)
        elif frame.message_type == MSG_DIAGNOSTIC:
            self._handle_diagnostic(frame.payload)

    def _handle_hello_ack(self, payload: bytes) -> None:
        ack = unpack_hello_ack(payload)
        if ack.board_role != BOARD_ROLE_SENSOR:
            self.get_logger().error(f"포트에 연결된 보드가 센서가 아님(role={ack.board_role})")
            return
        if ack.protocol_version != PROTOCOL_VERSION:
            self.get_logger().error(
                f"프로토콜 버전 불일치: 보드={ack.protocol_version} Jetson={PROTOCOL_VERSION}"
            )
            return
        self.get_logger().info(
            f"센서 ESP32 핸드셰이크 완료: fw={ack.firmware_major}.{ack.firmware_minor}.{ack.firmware_patch} "
            f"state={_state_name(ack.board_state)}"
        )

    def _handle_encoder_state(self, payload: bytes) -> None:
        state = unpack_encoder_state(payload)
        msg = String()
        msg.data = json.dumps(
            {
                "drive_encoder_ticks_left": state.drive_encoder_ticks_left,
                "drive_encoder_ticks_right": state.drive_encoder_ticks_right,
                "drive_speed_left_mmps": state.drive_speed_left_mmps,
                "drive_speed_right_mmps": state.drive_speed_right_mmps,
                "measured_steering_mdeg": state.measured_steering_mdeg,
                "sample_age_ms": state.sample_age_ms,
            }
        )
        self._encoder_pub.publish(msg)

    def _handle_environment_state(self, payload: bytes) -> None:
        state = unpack_environment_state(payload)
        msg = String()
        msg.data = json.dumps(
            {
                "temperature_deci_c": state.temperature_deci_c,
                "humidity_deci_pct": state.humidity_deci_pct,
                "status_flags": state.status_flags,
                "sample_age_ms": state.sample_age_ms,
            }
        )
        self._environment_pub.publish(msg)

    def _handle_proximity_state(self, payload: bytes) -> None:
        state = unpack_proximity_state(payload)
        msg = String()
        msg.data = json.dumps(
            {
                "front_min_distance_mm": state.front_min_distance_mm,
                "valid_sensor_mask": state.valid_sensor_mask,
                "protective_stop": bool(state.protective_stop),
                "sample_age_ms": state.sample_age_ms,
            }
        )
        self._proximity_pub.publish(msg)

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
    node = Esp32SensorBridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

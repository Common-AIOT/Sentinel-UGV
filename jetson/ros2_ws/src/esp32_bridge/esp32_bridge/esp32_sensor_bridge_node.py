"""센서 ESP32 브리지 노드 (S15P11A301-84, Phase 0 메시지 계약 정리).

USB 직렬(921600bps, COBS+CRC16)로 센서 ESP32(`esp32_sensor_comm` 스케치)와
통신하고, 수신한 페이로드를 **표준 ROS 메시지**로 발행한다.

    ENCODER_STATE     -> nav_msgs/Odometry        /wheel/odometry
    PROXIMITY_STATE   -> sensor_msgs/Range        /range/front
                         std_msgs/Bool            /proximity/protective_stop
    ENVIRONMENT_STATE -> sensor_msgs/Temperature  /environment/temperature
                         sensor_msgs/RelativeHumidity /environment/relative_humidity

이전 구현은 셋 다 `std_msgs/String` JSON으로 냈는데, 그러면 `robot_localization`
EKF도 Nav2 costmap/collision_monitor도 아무것도 구독할 수 없다. 자율주행 전
단계의 전제라 Phase 0에서 먼저 걷어냈다.

## 아직 없는 것

`sensor_msgs/Imu`(`/imu/data_raw`, 명세 23.2)는 IMU 부품이 미확정이고
(TBD-HW-012) 센서 ESP32에 `IMU_STATE` 메시지 자체가 없어 발행하지 않는다.
IMU가 붙으면 여기에 퍼블리셔를 추가한다.

`measured_steering_mdeg`는 조향 모터가 캐스터 휠로 대체되며 항상 0이라
발행하지 않는다.

## TF

`publish_odom_tf`는 기본 false다. 지금은 `slam.launch.py`가
`odom -> base_footprint`를 static identity로 발행하고 있어(그쪽 docstring 참고)
동시에 켜면 두 발행자가 같은 TF를 다투게 된다. `publish_static_odom:=false`로
static을 끈 뒤에 이 값을 true로 올리거나, Phase 3에서 `ekf_node`가
`/odometry/filtered`로 TF를 소유하게 한 뒤 계속 false로 둔다.

## 워치독 keep-alive

프로토콜 메시지 표(§34-5)에는 Jetson→센서 보드로 가는 주기적 트래픽이
HELLO/CONFIG뿐이라, 센서 보드의 300ms 통신 워치독에 입력을 공급할 수단이
없다(계획 §8, 열린 리스크 2). 그래서 이 노드가 HELLO를 ~6-7Hz(150ms 간격,
워치독 타임아웃의 절반 이하)로 keep-alive처럼 재전송한다 - 이는 문서에 없던
gap-fill 결정이며 구현 후 §34-7에 addendum으로 반영해야 한다.
"""

from __future__ import annotations

import math
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Range, RelativeHumidity, Temperature
from std_msgs.msg import Bool
from tf2_ros import TransformBroadcaster

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
from .wheel_odometry import (
    REJECT_BASELINE,
    OdometrySample,
    Pose2D,
    WheelOdometry,
    WheelOdometryConfig,
    default_meters_per_tick,
    yaw_to_quaternion,
)

_SENSOR_STATE_NAMES = ["BOOT", "STREAMING", "DEGRADED", "COMM_LOST"]

_FRAME_PARSE_ERRORS = (CobsError, LengthError, VersionError, UnknownMessageTypeError, CrcError)

# `sensor_task.cpp`의 §35-3 실측 전 임시 상수에서 유도한 기본 스케일.
# 실측이 끝나면 config/esp32_bridge.yaml의 meters_per_tick_left/right를 덮어쓴다.
_DEFAULT_METERS_PER_TICK = default_meters_per_tick(
    wheel_diameter_m=0.120, counts_per_encoder_rev=16384, gear_ratio=82.0
)

# 트랙폭은 실측값이 아예 없다(TBD-CAL-001, docs/06 CAL-04). 접지 중심 실측 전까지
# 임시값이며, 이 값이 그대로면 기동 시 경고를 띄운다.
_PLACEHOLDER_TRACK_WIDTH_M = 0.30

# `sensor_task.cpp`의 validSensorMask 비트 0 = 전방 HC-SR04.
_PROXIMITY_FRONT_MASK_BIT = 0x01

# `sensor_task.cpp`의 ENV_STATUS_VALID.
_ENVIRONMENT_STATUS_VALID = 0

# sample_age_ms가 이보다 크면 보드 쪽 계산 오류로 보고 스탬프를 보정하지 않는다.
_MAX_PLAUSIBLE_SAMPLE_AGE_MS = 1000

# 거부 샘플 로그 억제 간격. 실패가 이어져도 콘솔을 덮지 않게 한다.
_REJECT_LOG_PERIOD_S = 2.0


def _state_name(value: int) -> str:
    if 0 <= value < len(_SENSOR_STATE_NAMES):
        return _SENSOR_STATE_NAMES[value]
    return f"UNKNOWN({value})"


def _reliable_qos(depth: int = 10) -> QoSProfile:
    """RELIABLE/VOLATILE 프로파일.

    센서 데이터에 BEST_EFFORT를 쓰고 싶어지지만, RELIABLE 발행자는 BEST_EFFORT
    구독자도 그대로 받을 수 있는 반면 반대는 **조용히 아무것도 오지 않는다.**
    Nav2/robot_localization의 기본 구독 프로파일이 제각각이라 호환 범위가 넓은
    쪽으로 통일한다.
    """
    return QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.VOLATILE,
        history=HistoryPolicy.KEEP_LAST,
        depth=depth,
    )


def _latched_qos() -> QoSProfile:
    """마지막 값이 늦게 뜬 구독자에게도 전달되는 프로파일.

    보호 정지 상태는 `safety_gate`가 기동하자마자 알아야 하는 값이라
    TRANSIENT_LOCAL로 둔다.
    """
    return QoSProfile(
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
    )


class Esp32SensorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("esp32_sensor_bridge")

        self.declare_parameter("port", "/dev/sentinel_mcu_sensor")
        self.declare_parameter("baudrate", 921600)
        # 300ms 워치독의 절반 이하로 둬 한두 프레임 유실에도 트립되지 않게 한다.
        self.declare_parameter("keepalive_period_s", 0.15)

        # ---- 토픽·프레임 ----
        self.declare_parameter("odometry_topic", "/wheel/odometry")
        self.declare_parameter("range_topic", "/range/front")
        self.declare_parameter("protective_stop_topic", "/proximity/protective_stop")
        self.declare_parameter("temperature_topic", "/environment/temperature")
        self.declare_parameter("relative_humidity_topic", "/environment/relative_humidity")
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("base_frame_id", "base_footprint")
        self.declare_parameter("range_frame_id", "ultrasonic_front_link")
        self.declare_parameter("environment_frame_id", "base_link")
        self.declare_parameter("publish_odom_tf", False)

        # ---- 오도메트리 캘리브레이션 (TBD-CAL-001, §35-3) ----
        self.declare_parameter("meters_per_tick_left", _DEFAULT_METERS_PER_TICK)
        self.declare_parameter("meters_per_tick_right", _DEFAULT_METERS_PER_TICK)
        self.declare_parameter("track_width_m", _PLACEHOLDER_TRACK_WIDTH_M)
        self.declare_parameter("max_wheel_speed_mps", 2.0)
        self.declare_parameter(
            "odom_pose_covariance_diagonal", [0.05, 0.05, 1.0e6, 1.0e6, 1.0e6, 0.1]
        )
        self.declare_parameter(
            "odom_twist_covariance_diagonal", [0.02, 1.0e6, 1.0e6, 1.0e6, 1.0e6, 0.05]
        )

        # ---- HC-SR04 (sensor_task.cpp 상수와 일치시킬 것) ----
        self.declare_parameter("range_min_m", 0.02)
        self.declare_parameter("range_max_m", 4.0)
        self.declare_parameter("range_field_of_view_rad", 0.26)

        self._odom_frame_id = self.get_parameter("odom_frame_id").value
        self._base_frame_id = self.get_parameter("base_frame_id").value
        self._range_frame_id = self.get_parameter("range_frame_id").value
        self._environment_frame_id = self.get_parameter("environment_frame_id").value
        self._range_min_m = float(self.get_parameter("range_min_m").value)
        self._range_max_m = float(self.get_parameter("range_max_m").value)
        self._range_field_of_view_rad = float(self.get_parameter("range_field_of_view_rad").value)
        self._pose_covariance = self._covariance_from_diagonal("odom_pose_covariance_diagonal")
        self._twist_covariance = self._covariance_from_diagonal("odom_twist_covariance_diagonal")

        self._odometry = WheelOdometry(
            WheelOdometryConfig(
                meters_per_tick_left=float(self.get_parameter("meters_per_tick_left").value),
                meters_per_tick_right=float(self.get_parameter("meters_per_tick_right").value),
                track_width_m=float(self.get_parameter("track_width_m").value),
                max_wheel_speed_mps=float(self.get_parameter("max_wheel_speed_mps").value),
            )
        )
        self._warn_placeholder_calibration()

        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._reboot_detector = RebootDetector()
        self._last_reject_log_time = self.get_clock().now()

        port = self.get_parameter("port").value
        baudrate = self.get_parameter("baudrate").value
        self._transport = SerialTransport(port, baudrate, logger=self.get_logger())
        self._transport.open()

        self._odometry_pub = self.create_publisher(
            Odometry, self.get_parameter("odometry_topic").value, _reliable_qos()
        )
        self._range_pub = self.create_publisher(
            Range, self.get_parameter("range_topic").value, _reliable_qos()
        )
        self._protective_stop_pub = self.create_publisher(
            Bool, self.get_parameter("protective_stop_topic").value, _latched_qos()
        )
        self._temperature_pub = self.create_publisher(
            Temperature, self.get_parameter("temperature_topic").value, _reliable_qos(5)
        )
        self._relative_humidity_pub = self.create_publisher(
            RelativeHumidity, self.get_parameter("relative_humidity_topic").value, _reliable_qos(5)
        )
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self._tf_broadcaster = (
            TransformBroadcaster(self) if self.get_parameter("publish_odom_tf").value else None
        )
        if self._tf_broadcaster is None:
            self.get_logger().info(
                f"publish_odom_tf=false - {self._odom_frame_id}->{self._base_frame_id} TF는 "
                "발행하지 않는다(slam.launch.py의 static identity와 충돌 방지)"
            )

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        keepalive_period = self.get_parameter("keepalive_period_s").value
        self._keepalive_timer = self.create_timer(keepalive_period, self._send_hello)

    def destroy_node(self) -> bool:
        self._transport.close()
        return super().destroy_node()

    # ---- 설정 헬퍼 ----

    def _covariance_from_diagonal(self, parameter_name: str) -> list[float]:
        diagonal = [float(value) for value in self.get_parameter(parameter_name).value]
        if len(diagonal) != 6:
            raise ValueError(f"{parameter_name}은 6개 값이어야 한다: {diagonal}")
        covariance = [0.0] * 36
        for index, value in enumerate(diagonal):
            covariance[index * 7] = value
        return covariance

    def _warn_placeholder_calibration(self) -> None:
        config = self._odometry.config
        summary = (
            f"track_width_m={config.track_width_m} "
            f"meters_per_tick(L/R)={config.meters_per_tick_left:.4e}/"
            f"{config.meters_per_tick_right:.4e}"
        )
        still_placeholder = (
            math.isclose(config.track_width_m, _PLACEHOLDER_TRACK_WIDTH_M)
            or math.isclose(config.meters_per_tick_left, _DEFAULT_METERS_PER_TICK)
            or math.isclose(config.meters_per_tick_right, _DEFAULT_METERS_PER_TICK)
        )
        if still_placeholder:
            self.get_logger().warn(
                f"오도메트리 캘리브레이션이 §35-3 실측 전 임시값이다(TBD-CAL-001): {summary}. "
                "거리·각도 절대값을 신뢰하지 말 것"
            )
        else:
            self.get_logger().info(f"오도메트리 캘리브레이션: {summary}")

    # ---- 송신 ----

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

    # ---- 수신 ----

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
                # tick 카운터가 0으로 돌아가므로 기준점을 다시 잡는다. pose는
                # 유지된다(odom 프레임 연속성, REP-105).
                self._odometry.reset_encoder_origin()
                self.get_logger().warn("센서 ESP32 재부팅 감지 - 엔코더 기준점 재설정")

            self._dispatch(frame)

    def _dispatch(self, frame) -> None:
        if frame.message_type == MSG_HELLO_ACK:
            self._handle_hello_ack(frame.payload)
        elif frame.message_type == MSG_ENCODER_STATE:
            self._handle_encoder_state(frame.payload, frame.sender_uptime_ms)
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

    def _handle_encoder_state(self, payload: bytes, sender_uptime_ms: int) -> None:
        state = unpack_encoder_state(payload)
        result = self._odometry.update(
            state.drive_encoder_ticks_left,
            state.drive_encoder_ticks_right,
            sender_uptime_ms,
        )
        if result.sample is None:
            if result.reason != REJECT_BASELINE:
                self._log_rejected_sample(result.reason)
            return

        stamp = self._sample_stamp(state.sample_age_ms)
        self._odometry_pub.publish(self._build_odometry(result.sample, stamp))
        if self._tf_broadcaster is not None:
            self._tf_broadcaster.sendTransform(self._build_transform(result.sample.pose, stamp))

    def _handle_environment_state(self, payload: bytes) -> None:
        state = unpack_environment_state(payload)
        if state.status_flags != _ENVIRONMENT_STATUS_VALID:
            # 보드가 마지막 정상값을 그대로 들고 있어, 실패 상태에서 발행하면
            # 오래된 값이 새 측정처럼 보인다. 실패는 /diagnostics의
            # ENVIRONMENT_SENSOR_FAULT로만 드러낸다.
            return

        stamp = self._sample_stamp(state.sample_age_ms)

        temperature = Temperature()
        temperature.header.stamp = stamp
        temperature.header.frame_id = self._environment_frame_id
        temperature.temperature = state.temperature_deci_c / 10.0
        temperature.variance = 0.0  # DHT-11 분산 미측정 - 0은 "모름"을 뜻한다
        self._temperature_pub.publish(temperature)

        humidity = RelativeHumidity()
        humidity.header.stamp = stamp
        humidity.header.frame_id = self._environment_frame_id
        # sensor_msgs/RelativeHumidity는 0~1 비율이다(deci-percent가 아니라).
        humidity.relative_humidity = state.humidity_deci_pct / 1000.0
        humidity.variance = 0.0
        self._relative_humidity_pub.publish(humidity)

    def _handle_proximity_state(self, payload: bytes) -> None:
        state = unpack_proximity_state(payload)

        message = Range()
        message.header.stamp = self._sample_stamp(state.sample_age_ms)
        message.header.frame_id = self._range_frame_id
        message.radiation_type = Range.ULTRASOUND
        message.field_of_view = self._range_field_of_view_rad
        message.min_range = self._range_min_m
        message.max_range = self._range_max_m

        distance_m = state.front_min_distance_mm / 1000.0
        if not state.valid_sensor_mask & _PROXIMITY_FRONT_MASK_BIT:
            # 신뢰할 수 없는 샘플(최소거리 미만 반사 등). 스트림을 끊으면
            # collision_monitor가 정지하므로 값만 무효로 표시한다.
            message.range = math.inf
        elif distance_m >= self._range_max_m:
            # 보드는 에코 타임아웃을 "5m 밖 장애물 없음"으로 보고 max로 clamp해
            # 보낸다. 그대로 두면 4m 지점의 실제 장애물처럼 읽히므로 +Inf로
            # 바꾼다(sensor_msgs/Range의 "미검출" 관례). 정확히 4m에 있는 실제
            # 장애물도 같은 값이 되지만, HC-SR04 유효 범위 끝단이라 "미검출"로
            # 해석하는 쪽이 옳다.
            message.range = math.inf
        else:
            message.range = distance_m
        self._range_pub.publish(message)

        self._protective_stop_pub.publish(Bool(data=bool(state.protective_stop)))

    def _handle_diagnostic(self, payload: bytes) -> None:
        diag = unpack_diagnostic(payload)
        board_status = build_status(
            hardware_id=self.get_parameter("port").value,
            board_role=diag.board_role,
            board_state_name=_state_name(diag.board_state),
            fault_flags=diag.fault_flags,
            crc_error_count=diag.crc_error_count,
            dropped_frame_count=diag.dropped_frame_count,
            stale_sequence_count=diag.stale_sequence_count,
        )
        self._diagnostics_pub.publish(
            build_array(
                self.get_clock().now().to_msg(),
                [board_status, self._build_odometry_status()],
            )
        )

    # ---- 메시지 조립 ----

    def _sample_stamp(self, sample_age_ms: int):
        """보드가 보고한 측정 경과 시간만큼 현재 시각을 되돌린다.

        직렬 전송·큐 지연이 그대로 TF 시간 오차가 되는 것을 줄인다.
        """
        now = self.get_clock().now()
        if 0 < sample_age_ms <= _MAX_PLAUSIBLE_SAMPLE_AGE_MS:
            now = now - Duration(nanoseconds=sample_age_ms * 1_000_000)
        return now.to_msg()

    def _build_odometry(self, sample: OdometrySample, stamp) -> Odometry:
        message = Odometry()
        message.header.stamp = stamp
        message.header.frame_id = self._odom_frame_id
        message.child_frame_id = self._base_frame_id

        message.pose.pose.position.x = sample.pose.x
        message.pose.pose.position.y = sample.pose.y
        (
            message.pose.pose.orientation.x,
            message.pose.pose.orientation.y,
            message.pose.pose.orientation.z,
            message.pose.pose.orientation.w,
        ) = yaw_to_quaternion(sample.pose.yaw)
        message.pose.covariance = self._pose_covariance

        # 차동 구동은 횡방향 속도가 정의상 0이며 측정하지도 않는다. twist
        # covariance의 y/z/roll/pitch를 크게 둬 EKF가 융합하지 않게 한다.
        message.twist.twist.linear.x = sample.linear_x
        message.twist.twist.angular.z = sample.angular_z
        message.twist.covariance = self._twist_covariance
        return message

    def _build_transform(self, pose: Pose2D, stamp) -> TransformStamped:
        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = self._odom_frame_id
        transform.child_frame_id = self._base_frame_id
        transform.transform.translation.x = pose.x
        transform.transform.translation.y = pose.y
        (
            transform.transform.rotation.x,
            transform.transform.rotation.y,
            transform.transform.rotation.z,
            transform.transform.rotation.w,
        ) = yaw_to_quaternion(pose.yaw)
        return transform

    def _build_odometry_status(self) -> DiagnosticStatus:
        """캘리브레이션 중 tick 글리치·보드 재부팅을 눈으로 볼 수 있게 한다."""
        pose = self._odometry.pose
        config = self._odometry.config
        return DiagnosticStatus(
            level=DiagnosticStatus.OK,
            name="esp32_bridge: WHEEL_ODOMETRY",
            message=f"x={pose.x:.3f} y={pose.y:.3f} yaw={math.degrees(pose.yaw):.1f}deg",
            hardware_id=self.get_parameter("port").value,
            values=[
                KeyValue(key="rejected_sample_count", value=str(self._odometry.rejected_sample_count)),
                KeyValue(
                    key="encoder_origin_reset_count",
                    value=str(self._odometry.encoder_origin_reset_count),
                ),
                KeyValue(key="track_width_m", value=f"{config.track_width_m:.4f}"),
                KeyValue(key="meters_per_tick_left", value=f"{config.meters_per_tick_left:.6e}"),
                KeyValue(key="meters_per_tick_right", value=f"{config.meters_per_tick_right:.6e}"),
                KeyValue(key="publish_odom_tf", value=str(self._tf_broadcaster is not None)),
            ],
        )

    def _log_rejected_sample(self, reason: str) -> None:
        now = self.get_clock().now()
        if now - self._last_reject_log_time < Duration(seconds=_REJECT_LOG_PERIOD_S):
            return
        self._last_reject_log_time = now
        self.get_logger().warn(
            f"엔코더 샘플 거부({reason}) - 누적 {self._odometry.rejected_sample_count}건"
        )


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

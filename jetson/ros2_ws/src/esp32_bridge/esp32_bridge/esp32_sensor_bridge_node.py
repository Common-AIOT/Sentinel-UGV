"""센서 ESP32 브리지 노드 (S15P11A301-84, Phase 0 메시지 계약 정리).

USB 직렬(921600bps, COBS+CRC16)로 센서 ESP32(`esp32_sensor_comm` 스케치)와
통신하고, 수신한 페이로드를 **표준 ROS 메시지**로 발행한다.

    ENCODER_STATE     -> nav_msgs/Odometry        /wheel/odometry
    IMU_STATE         -> sensor_msgs/Imu          /imu/data_raw
    PROXIMITY_STATE   -> sensor_msgs/Range        /range/front
                         std_msgs/Bool            /proximity/protective_stop
    ENVIRONMENT_STATE -> sensor_msgs/Temperature  /environment/temperature
                         sensor_msgs/RelativeHumidity /environment/relative_humidity

이전 구현은 셋 다 `std_msgs/String` JSON으로 냈는데, 그러면 `robot_localization`
EKF도 Nav2 costmap/collision_monitor도 아무것도 구독할 수 없다. 자율주행 전
단계의 전제라 Phase 0에서 먼저 걷어냈다.

## IMU (S15P11A301-244)

`IMU_STATE`(0x26)는 MPU6050 원시 gyro/accel을 100Hz로 담아 온다. 발행 규칙은 셋이다.

1. **타임스탬프는 `sample_time_us`에서 만든다.** 수신 시각을 측정 시각으로 대신하지
   않는다(§34-5). 보드 monotonic µs -> ROS 시각 변환은 `imu_clock.BoardClockOffset`이
   min filter로 추정하며, 보드 재부팅을 감지하면 offset을 버리고 다시 동기화한다.
2. **`BUS_ERROR`면 발행하지 않는다.** 판독이 실패하면 보드는 마지막 값을 그대로 들고
   `status_flags`만 `BUS_ERROR`로 바꿔 보낸다(`sensor_task.cpp`). 그대로 내보내면
   오래된 값이 새 측정처럼 보이므로 DHT-11과 같은 규칙을 쓴다 - 실패는
   `/diagnostics`의 `IMU_SENSOR_FAULT`로만 드러낸다.
3. **`CALIBRATING`/`RANGE_ERROR`거나 clock offset이 아직 안정되지 않았으면 공분산을
   크게 실어 발행한다.** 스트림을 끊으면 축 정렬 검증(TESTING.md 10-4)에 쓸 값이
   사라지고, 그대로 신뢰하면 EKF가 바이어스 미보정 자이로를 융합한다. 값은 보여 주고
   융합만 막는 것이 `sensor_msgs/Imu`의 공분산 관례에 맞다.

`orientation`은 채우지 않는다. 원시 출력에 자세 융합이 없으므로 REP-145 관례대로
`orientation_covariance[0] = -1`로 "추정값 없음"을 표시한다.

같은 IMU 샘플이 두 프레임에 실려 오는 경우가 있다(comm_task 송신 주기 10ms와 센서
태스크 샘플 주기 10ms가 서로 독립이다). `sample_time_us`가 이전과 같으면 같은 측정의
재전송이므로 버린다 - 같은 스탬프의 메시지 두 개는 EKF에서 한 측정을 두 번 세는 것이
된다. 그래서 `ros2 topic hz /imu/data_raw`는 100Hz보다 조금 낮게 나올 수 있다.

`measured_steering_mdeg`는 항상 0이라 발행하지 않는다. 2026-08-06 전륜 조향이
복구됐지만 **조향은 개루프**다 - DS51150 서보가 내부 폐루프로 각도를 유지하고
외부로 각도를 출력하지 않으므로 실제 조향각을 재는 수단이 없다(§34-5). 이 자리를
쓰려면 앞바퀴 킹핀이나 타이로드에 별도 각도 센서를 달아야 하고 현재 구성에 없다.
Jetson이 볼 수 있는 조향 값은 모터 채널의 `DRIVE_STATE`(목표각·서보 지령값)뿐이며
그것은 명령이지 측정이 아니다.

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
import struct
import threading

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import Imu, Range, RelativeHumidity, Temperature
from std_msgs.msg import Bool
from tf2_ros import TransformBroadcaster

from .diagnostics import RebootDetector, build_array, build_status
from .imu_clock import BoardClockOffset
from .packet_codec import (
    CobsError,
    CrcError,
    LengthError,
    ProtocolError,
    UnknownMessageTypeError,
    VersionError,
    build_frame,
    parse_frame,
    unpack_diagnostic,
    unpack_encoder_state,
    unpack_environment_state,
    unpack_hello_ack,
    unpack_imu_state,
    unpack_proximity_state,
)
from .protocol_constants import (
    BOARD_ROLE_SENSOR,
    IMU_STATUS_BUS_ERROR,
    IMU_STATUS_VALID,
    IMU_TEMPERATURE_INVALID,
    MSG_DIAGNOSTIC,
    MSG_ENCODER_STATE,
    MSG_ENVIRONMENT_STATE,
    MSG_HELLO,
    MSG_HELLO_ACK,
    MSG_IMU_STATE,
    MSG_PROXIMITY_STATE,
    PROTOCOL_VERSION,
    imu_status_flag_names,
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

# sensor_msgs/Imu 관례: orientation_covariance[0] = -1 은 "자세 추정값 없음"을 뜻한다
# (REP-145). MPU6050 원시 출력에는 융합이 없으므로 이 값을 쓴다.
_ORIENTATION_UNAVAILABLE = -1.0

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


def _isotropic_covariance(variance: float) -> list[float]:
    """3x3 대각 공분산(행 우선 9개). MPU6050은 축별 잡음 실측이 없어 등방으로 둔다."""
    return [variance, 0.0, 0.0, 0.0, variance, 0.0, 0.0, 0.0, variance]


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
        self.declare_parameter("imu_topic", "/imu/data_raw")
        self.declare_parameter("range_topic", "/range/front")
        self.declare_parameter("protective_stop_topic", "/proximity/protective_stop")
        self.declare_parameter("temperature_topic", "/environment/temperature")
        self.declare_parameter("relative_humidity_topic", "/environment/relative_humidity")
        self.declare_parameter("odom_frame_id", "odom")
        self.declare_parameter("base_frame_id", "base_footprint")
        self.declare_parameter("imu_frame_id", "imu_link")
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

        # ---- MPU6050 IMU (S15P11A301-244, TBD-CAL: 진동·bias 실측 전 임시값) ----
        self.declare_parameter("imu_angular_velocity_variance", 4.0e-4)
        self.declare_parameter("imu_linear_acceleration_variance", 4.0e-2)
        # CALIBRATING/RANGE_ERROR·clock offset 미안정 구간에 싣는 공분산. EKF가
        # 융합하지 않을 만큼 크되 유한해야 한다(NaN/Inf는 구독자를 깨뜨린다).
        self.declare_parameter("imu_untrusted_variance", 1.0e6)
        # 보드 monotonic 시계 오프셋 재동기 주기. 창 길이 × 크리스털 드리프트가
        # 타임스탬프 오차 상한이 된다(10s·100ppm = 1ms).
        self.declare_parameter("imu_clock_resync_period_s", 10.0)

        # ---- HC-SR04 (sensor_task.cpp 상수와 일치시킬 것) ----
        self.declare_parameter("range_min_m", 0.02)
        self.declare_parameter("range_max_m", 4.0)
        self.declare_parameter("range_field_of_view_rad", 0.26)

        self._odom_frame_id = self.get_parameter("odom_frame_id").value
        self._base_frame_id = self.get_parameter("base_frame_id").value
        self._imu_frame_id = self.get_parameter("imu_frame_id").value
        self._range_frame_id = self.get_parameter("range_frame_id").value
        self._environment_frame_id = self.get_parameter("environment_frame_id").value
        self._range_min_m = float(self.get_parameter("range_min_m").value)
        self._range_max_m = float(self.get_parameter("range_max_m").value)
        self._range_field_of_view_rad = float(self.get_parameter("range_field_of_view_rad").value)
        self._pose_covariance = self._covariance_from_diagonal("odom_pose_covariance_diagonal")
        self._twist_covariance = self._covariance_from_diagonal("odom_twist_covariance_diagonal")

        self._imu_gyro_covariance = _isotropic_covariance(
            float(self.get_parameter("imu_angular_velocity_variance").value)
        )
        self._imu_accel_covariance = _isotropic_covariance(
            float(self.get_parameter("imu_linear_acceleration_variance").value)
        )
        self._imu_untrusted_covariance = _isotropic_covariance(
            float(self.get_parameter("imu_untrusted_variance").value)
        )
        self._imu_clock = BoardClockOffset(
            resync_period_s=float(self.get_parameter("imu_clock_resync_period_s").value)
        )
        self._imu_published_count = 0
        self._imu_skipped_bus_error_count = 0
        self._imu_duplicate_sample_count = 0
        self._imu_last_sample_time_us: int | None = None
        self._imu_last_status_flags = 0
        self._imu_last_temperature_centi_c = IMU_TEMPERATURE_INVALID

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
        self._malformed_payload_count = 0
        self._last_malformed_log_time = self.get_clock().now()

        port = self.get_parameter("port").value
        baudrate = self.get_parameter("baudrate").value
        self._transport = SerialTransport(port, baudrate, logger=self.get_logger())
        self._transport.open()

        self._odometry_pub = self.create_publisher(
            Odometry, self.get_parameter("odometry_topic").value, _reliable_qos()
        )
        # 100Hz라 depth를 조금 크게 둔다(50Hz 오도메트리보다 큐가 빨리 찬다).
        self._imu_pub = self.create_publisher(
            Imu, self.get_parameter("imu_topic").value, _reliable_qos(20)
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
            # HELLO 는 150ms 마다 나가므로 포트가 끊긴 동안 이 경고가 6.7Hz 로
            # 쌓인다. S15P11A301-264 관찰 구간에서 로그가 6142줄이 됐다.
            # 억제해도 정보는 잃지 않는다 — 재연결 성공·실패 전이는
            # SerialTransport 가 따로 남긴다.
            self.get_logger().warning(
                f"HELLO 전송 실패: {exc}", throttle_duration_sec=5.0
            )

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
                # 유지된다(odom 프레임 연속성, REP-105). IMU는 monotonic 시계도
                # 0으로 돌아가므로 clock offset을 폐기하고 다시 동기화한다(§34-5).
                self._odometry.reset_encoder_origin()
                self._imu_clock.reset()
                self._imu_last_sample_time_us = None
                self.get_logger().warn(
                    "센서 ESP32 재부팅 감지 - 엔코더 기준점·IMU clock offset 재설정"
                )

            try:
                self._dispatch(frame)
            except (ProtocolError, struct.error) as exc:
                # 페이로드 길이가 타입 정의와 다른 경우(펌웨어/브리지 버전 불일치).
                # 이 스레드가 죽으면 모든 토픽이 "오류 없이 조용히" 멈추므로 반드시
                # 잡는다 - 이 저장소에서 가장 진단하기 어려운 실패 모드다.
                self._log_malformed_payload(frame.message_type, exc)

    def _dispatch(self, frame) -> None:
        if frame.message_type == MSG_HELLO_ACK:
            self._handle_hello_ack(frame.payload)
        elif frame.message_type == MSG_ENCODER_STATE:
            self._handle_encoder_state(frame.payload, frame.sender_uptime_ms)
        elif frame.message_type == MSG_IMU_STATE:
            self._handle_imu_state(frame.payload)
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

    def _handle_imu_state(self, payload: bytes) -> None:
        state = unpack_imu_state(payload)
        self._imu_last_status_flags = state.status_flags
        self._imu_last_temperature_centi_c = state.temperature_centi_c

        if state.status_flags & IMU_STATUS_BUS_ERROR:
            # 보드가 마지막 값을 그대로 들고 있다. 오래된 값을 새 측정처럼 내보내지
            # 않는다(DHT-11과 같은 규칙). 실패는 /diagnostics로만 드러낸다.
            self._imu_skipped_bus_error_count += 1
            return

        if state.sample_time_us == self._imu_last_sample_time_us:
            # 같은 측정의 재전송. 같은 스탬프로 두 번 발행하면 EKF가 한 측정을
            # 두 번 센다.
            self._imu_duplicate_sample_count += 1
            return
        self._imu_last_sample_time_us = state.sample_time_us

        receive_time = self.get_clock().now()
        stamp_ns = self._imu_clock.update(state.sample_time_us, receive_time.nanoseconds)

        message = Imu()
        message.header.stamp = Time(nanoseconds=stamp_ns).to_msg()
        message.header.frame_id = self._imu_frame_id

        # MPU6050 원시 출력에는 자세 융합이 없다. 항등 쿼터니언을 넣고 공분산으로
        # "추정값 없음"을 표시한다(REP-145) - 이것을 빼면 EKF가 항등 자세를 실제
        # 관측으로 융합해 로봇이 계속 정면을 본다고 믿는다.
        message.orientation.w = 1.0
        message.orientation_covariance = [0.0] * 9
        message.orientation_covariance[0] = _ORIENTATION_UNAVAILABLE

        message.angular_velocity.x = state.gyro_x_radps
        message.angular_velocity.y = state.gyro_y_radps
        message.angular_velocity.z = state.gyro_z_radps
        message.linear_acceleration.x = state.accel_x_mps2
        message.linear_acceleration.y = state.accel_y_mps2
        message.linear_acceleration.z = state.accel_z_mps2

        # VALID 단독이 아니거나(CALIBRATING·RANGE_ERROR) clock offset이 아직
        # 안정되지 않았으면 값은 보여 주고 융합만 막는다(§34-5).
        trusted = state.status_flags == IMU_STATUS_VALID and self._imu_clock.settled
        message.angular_velocity_covariance = (
            self._imu_gyro_covariance if trusted else self._imu_untrusted_covariance
        )
        message.linear_acceleration_covariance = (
            self._imu_accel_covariance if trusted else self._imu_untrusted_covariance
        )

        self._imu_pub.publish(message)
        self._imu_published_count += 1

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
                [board_status, self._build_odometry_status(), self._build_imu_status()],
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

        # 횡방향 속도는 이 모델(후륜 기준 자전거 모델)에서 정의상 0이며 측정하지도
        # 않는다. twist covariance의 y/z/roll/pitch를 크게 둬 EKF가 융합하지 않게 한다.
        # angular_z 도 EKF 입력이 아니다 — 선회 중 후륜 스크럽 때문에 좌·우 속도 차가
        # 기하와 맞지 않으므로 yaw 는 IMU 가 담당한다(§35-3, wheel_odometry docstring).
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

    def _build_imu_status(self) -> DiagnosticStatus:
        """`/imu/data_raw`가 조용할 때 왜 조용한지 여기서 읽는다.

        발행 중단 사유가 셋(BUS_ERROR·중복 샘플·아직 프레임 미수신)이고 토픽만
        보면 구별되지 않는다. 카운터를 함께 낸다.
        """
        flags = self._imu_last_status_flags
        names = imu_status_flag_names(flags)

        if self._imu_published_count == 0 and self._imu_skipped_bus_error_count == 0:
            level = DiagnosticStatus.WARN
            message = "IMU_STATE 프레임 미수신"
        elif flags & IMU_STATUS_BUS_ERROR:
            level = DiagnosticStatus.ERROR
            message = "BUS_ERROR - 발행 중단(오래된 값을 새 측정으로 내보내지 않는다)"
        elif flags != IMU_STATUS_VALID or not self._imu_clock.settled:
            level = DiagnosticStatus.WARN
            message = "발행 중 - 공분산을 크게 실어 EKF 융합에서 제외"
        else:
            level = DiagnosticStatus.OK
            message = "VALID"

        temperature = self._imu_last_temperature_centi_c
        temperature_text = (
            "invalid" if temperature == IMU_TEMPERATURE_INVALID else f"{temperature / 100.0:.2f}"
        )
        offset_ns = self._imu_clock.offset_ns
        offset_text = "unset" if offset_ns is None else f"{offset_ns / 1e6:.3f}"

        return DiagnosticStatus(
            level=level,
            name="esp32_bridge: IMU",
            message=message,
            hardware_id=self.get_parameter("port").value,
            values=[
                KeyValue(key="status_flags", value=",".join(names) if names else "NONE"),
                KeyValue(key="published_count", value=str(self._imu_published_count)),
                KeyValue(
                    key="skipped_bus_error_count", value=str(self._imu_skipped_bus_error_count)
                ),
                KeyValue(
                    key="duplicate_sample_count", value=str(self._imu_duplicate_sample_count)
                ),
                KeyValue(key="clock_offset_ms", value=offset_text),
                KeyValue(key="clock_offset_settled", value=str(self._imu_clock.settled)),
                KeyValue(key="clock_resync_count", value=str(self._imu_clock.resync_count)),
                KeyValue(key="temperature_c", value=temperature_text),
                KeyValue(key="malformed_payload_count", value=str(self._malformed_payload_count)),
            ],
        )

    def _log_malformed_payload(self, message_type: int, exc: Exception) -> None:
        self._malformed_payload_count += 1
        now = self.get_clock().now()
        if now - self._last_malformed_log_time < Duration(seconds=_REJECT_LOG_PERIOD_S):
            return
        self._last_malformed_log_time = now
        self.get_logger().error(
            f"페이로드 해석 실패(type=0x{message_type:02x}): {exc} - 누적 "
            f"{self._malformed_payload_count}건. 펌웨어와 브리지의 프로토콜 버전을 확인할 것"
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

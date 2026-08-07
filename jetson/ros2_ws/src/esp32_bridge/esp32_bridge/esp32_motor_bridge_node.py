"""모터 ESP32 브리지 노드 (S15P11A301-84, 프레이밍 재작성 S15P11A301-321).

USB 직렬(921600bps)로 모터 ESP32(`esp32_motor_comm` 스케치)와 통신한다. 범위는
통신 계층뿐이다 - 실제 BTS7960 PWM·조향 서보 PWM 생성은 펌웨어가, 차량 기구학
변환(v/ω → 후륜 속도 + 전륜 조향각)은 `sentinel_drive`의 `vehicle_kinematics_node`가
담당한다(§34-11). `~/drive_command`는 전용 `esp32_bridge_msgs` 인터페이스 대신
JSON 문자열(std_msgs/String)로 받는다(계획 §5).

## S15P11A301-321: 프레이밍을 motor_packet_codec(동기워드+고정길이+CRC8)으로 교체

실측된 무응답 사례가 `rx_frame_count=0`(바이트 자체가 안 옴)이었던 것과 별개로,
이 재작성은 두 가지를 한다:

1. **프레이밍 단순화**: 옛 COBS+CRC16+길이+uptime(`packet_codec.py`, 센서가 여전히
   씀)을 이 노드에서만 동기워드+고정27바이트+CRC8로 바꿨다. 메시지 종류·의미는
   그대로다 - 프레이밍만 바뀌었다.
2. **링크-자체-생존 keepalive 추가**: 이 노드에는 핸드셰이크 성공 전에만 HELLO를
   재시도하는 타이머만 있었다 - 핸드셰이크 이후로는 `~/drive_command`가 계속
   들어오지 않으면 이 노드가 만드는 트래픽이 하나도 없었다. 센서 브리지는 처음부터
   150ms마다 영구히 HELLO를 재전송하는 keepalive가 있어 "링크 자체가 죽었나"와
   "상위 파이프라인이 명령을 안 준다"가 섞이지 않는데, 모터 쪽엔 이 장치가
   없었다. 이제 `keepalive_period_s`(기본 0.15, 센서와 동일 근거 - 300ms 워치독의
   절반 이하) 타이머가 핸드셰이크 여부와 무관하게 영원히 HELLO를 보낸다. 펌웨어는
   이 HELLO를 포함해 어떤 유효 프레임이든 받으면 `link_silence_ms`(DIAGNOSTIC)를
   리셋한다 - 이 값과 `FAULT_COMM_TIMEOUT_MOTOR`(DRIVE_COMMAND 수신 빈도만 보는
   `mode_arbiter`의 축)를 같이 읽으면 "링크가 죽었다"와 "상위가 명령을 안 준다"를
   구분할 수 있다(`hardware/esp32/motor/esp32_motor_comm/motor_protocol.h` 참고).

**senderUptimeMs가 없어졌다.** 새 프레이밍은 그 필드를 없앴고, 그 결과 이 노드는
더 이상 uptime 감소로 재부팅을 감지하지 않는다(`RebootDetector`는 센서 노드에서만
쓴다). 재부팅해도 `MotorBoardState`가 그대로 `SAFE_IDLE`로 보이므로 이 경로로는
구분이 안 된다 - 대신 위 keepalive가 재부팅 직후에도 곧장 HELLO_ACK을 받아
핸드셰이크를 다시 확인시켜 주므로, 실질적인 안전 영향은 없다(받아들인 단순화).

시동 시 HELLO/HELLO_ACK 핸드셰이크로 버전·role을 확인한다(§34-6). 300ms 통신
워치독과 실제 정지는 ESP32 펌웨어가 로컬로 수행하므로(esp32_motor_comm의
control_task), 이 노드가 죽거나 재시작해도 모터 ESP32는 독립적으로 안전하게
정지한다 - 이 노드는 명령을 전달하고 상태를 보고한다.

예외가 하나 있다. 초음파 `protective_stop` → `STOP_COMMAND` **중계**는 이 노드가
판정한다(S15P11A301-237, 명세 03-276). 통신 계층에 판정을 두지 않는다는 위
원칙의 예외이며, 그 근거는 `protective_relay` 모듈 docstring 에 있다 - 요지는
지연(홉을 늘리지 않는다)과 독립성(`safety_gate` 가 죽어도 보호정지는 전달돼야
한다)이다.

**예외는 그 하나뿐이다.** `~/set_mode`(S15P11A301-298)는 예외가 아니라
`~/drive_command` 와 같은 모양의 순수 중계다 - 판정은 `mission_manager` 의
`mode_gateway` 가 하고 여기서는 이름을 바이트로 옮기기만 한다.

## S15P11A301-323: 무응답과 파싱 실패를 구분하는 링크 진단

`link_health.motor_link_verdict()` 가 판정하고 `_publish_link_status()` 가 보드
`DIAGNOSTIC` 수신 여부와 무관하게 `link_report_period_s`(기본 1Hz)마다
`/diagnostics` 에 `esp32_bridge: MOTOR_LINK` 항목을 낸다 - 종전에는 보드가
죽으면 이 항목 자체가 없어서 "항목 없음"과 "정상"이 화면에서 구별되지 않았다.
`_rx_frame_count`(동기+CRC8까지 통과해 프레임으로 선 횟수)와
`_parse_error_count`(그중 파싱까지 실패한 횟수)를 따로 세어, 0 인가 아닌가로
"바이트 자체가 안 온다"와 "오기는 하는데 해석이 안 된다"를 가른다. 핸드셰이크가
`handshake_warn_after_attempts`(기본 34회 ≈ keepalive 0.15s 기준 5초)를 넘도록
안 되면 `_send_hello()` 가 이 두 경우를 구분한 ERROR 로그를 한 번 낸다.

이 링크 진단(`rx_frame_count` 등)은 젯슨 쪽에서 본 raw 바이트 도착 여부이고,
`MotorDiagnostic.linkSilenceMs`(위 S15P11A301-321)는 ESP32 펌웨어 쪽에서 본
Jetson 프레임 수신 여부다 - 방향이 반대라 서로를 대신하지 않는다. 양쪽을 같이
보면 어느 쪽 USB 구간이 죽었는지까지 좁혀진다.
"""

from __future__ import annotations

import json
import threading
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger

from .diagnostics import build_array, build_status
from .link_health import ERROR, OK, WARN, motor_link_verdict
from .protective_relay import ProtectiveStopRelay
from .motor_packet_codec import (
    CrcError,
    DriveCommand,
    LengthError,
    SetMode,
    SyncError,
    build_motor_frame,
    pack_drive_command,
    pack_set_mode,
    parse_motor_frame,
    unpack_command_ack,
    unpack_drive_state,
    unpack_hello_ack,
    unpack_motor_diagnostic,
)
from .motor_protocol_constants import (
    ACK_RESULT_ACCEPTED,
    BOARD_MODE_VALUES,
    BOARD_ROLE_MOTOR,
    MOTOR_PROTOCOL_VERSION,
    MSG_COMMAND_ACK,
    MSG_DIAGNOSTIC,
    MSG_DRIVE_COMMAND,
    MSG_DRIVE_STATE,
    MSG_ESTOP_COMMAND,
    MSG_HELLO,
    MSG_HELLO_ACK,
    MSG_SET_MODE,
    MSG_STOP_COMMAND,
    ack_result_name,
    message_type_name,
)
from .motor_serial_transport import MotorSerialTransport

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

_FRAME_PARSE_ERRORS = (LengthError, SyncError, CrcError)

# `link_health` 는 ROS 타입을 모른다(CI 에서 돌아야 한다). 매핑은 여기 둔다.
_DIAGNOSTIC_LEVELS = {
    OK: DiagnosticStatus.OK,
    WARN: DiagnosticStatus.WARN,
    ERROR: DiagnosticStatus.ERROR,
}


def _state_name(value: int) -> str:
    if 0 <= value < len(_MOTOR_STATE_NAMES):
        return _MOTOR_STATE_NAMES[value]
    return f"UNKNOWN({value})"


class Esp32MotorBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("esp32_motor_bridge")

        self.declare_parameter("port", "/dev/sentinel_mcu_motor")
        self.declare_parameter("baudrate", 921600)
        # 300ms 통신 워치독의 절반 이하 - §34-7 gap-fill, 센서 브리지와 같은 근거
        # (S15P11A301-321 전에는 이 노드에 keepalive가 아예 없었다).
        self.declare_parameter("keepalive_period_s", 0.15)
        # 링크 진단 발행 주기 (S15P11A301-323). 보드 DIAGNOSTIC(5Hz)과 달리 **보드가
        # 죽어 있을 때도** 나가야 하는 값이라 이쪽 시계로 낸다.
        self.declare_parameter("link_report_period_s", 1.0)
        # 이 횟수만큼 HELLO 를 보내고도 ACK 이 없으면 경고한다. keepalive_period_s
        # 기본값(0.15s) 기준 34회 ≈ 5.1초 - 부팅 순서상 브리지가 보드보다 먼저 뜨는
        # 정상 구간을 넘긴 값이다(S15P11A301-321 전 handshake_retry_period_s=1.0s
        # 기준으로는 5회였다 - keepalive 주기가 빨라진 만큼 횟수를 올렸다).
        self.declare_parameter("handshake_warn_after_attempts", 34)
        # 초음파 보호정지 중계 (S15P11A301-237, 명세 03-276). 근거와 왜 게이트가
        # 아니라 여기인지는 protective_relay 모듈 docstring 에 있다.
        self.declare_parameter("protective_stop_topic", "/proximity/protective_stop")
        self.declare_parameter("protective_stop_reassert_period_s", 0.2)
        # 중계를 끄는 문. 보호정지 신호를 만드는 센서 보드 없이 모터만 붙여
        # 시험할 때 쓴다. 기본은 켜짐이다 — 안전 경로를 기본으로 꺼 두면
        # 켜는 것을 잊은 구성이 "되니까" 로 굳는다.
        self.declare_parameter("relay_protective_stop", True)

        port = self.get_parameter("port").value
        baudrate = self.get_parameter("baudrate").value

        self._sequence = 0
        self._sequence_lock = threading.Lock()
        # 조향 필드가 빠진 명령에서 쓸 마지막 값. 펌웨어도 부팅 시 중립에서
        # 시작하므로 초기값 0이 맞다(§34-6).
        self._last_steering_mdeg = 0
        self._last_steering_rate_mdps = 0
        self._handshake_ok = False
        # 링크 계측 (S15P11A301-323). 보드가 죽어도 이 노드는 살아 있으므로,
        # **무엇을 못 받고 있는지**를 이 값들이 말한다.
        self._rx_frame_count = 0
        self._parse_error_count = 0
        self._parse_errors_by_type: dict[str, int] = {}
        self._hello_sent_count = 0
        self._last_rx_monotonic: float | None = None
        self._handshake_warned = False

        self._transport = MotorSerialTransport(port, baudrate, logger=self.get_logger())
        self._transport.open()

        self._drive_state_pub = self.create_publisher(String, "~/drive_state", 10)
        self._command_ack_pub = self.create_publisher(String, "~/command_ack", 10)
        self._diagnostics_pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)

        self.create_subscription(String, "~/drive_command", self._on_drive_command, 10)
        # 모드 전환 중계 (S15P11A301-298). `~/drive_command` 와 같은 모양의 **순수
        # 중계**이며 판단이 아니다. RELIABLE 이어야 한다: 잃으면 mission_manager 가
        # 500ms 뒤 MOTOR_BOARD_NO_ACK 를 내고 운영자는 「보드가 죽었다」로 읽는데,
        # 실제로는 프레임이 나가지도 않은 것이다.
        self.create_subscription(
            String,
            "~/set_mode",
            self._on_set_mode,
            QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE,
                history=HistoryPolicy.KEEP_LAST,
                depth=10,
            ),
        )
        self.create_service(Trigger, "~/stop", self._on_stop_service)
        self.create_service(Trigger, "~/estop", self._on_estop_service)

        self._relay = ProtectiveStopRelay(
            reassert_period_s=float(
                self.get_parameter("protective_stop_reassert_period_s").value
            )
        )
        if self.get_parameter("relay_protective_stop").value:
            # 발행자가 TRANSIENT_LOCAL 이라 여기도 맞춰야 기동 직후 이미 눌려
            # 있는 상태를 받는다. VOLATILE 로 두면 다음 프레임까지 모르고,
            # 그 사이는 보호정지가 걸린 채로 STOP_COMMAND 가 안 나간다.
            self.create_subscription(
                Bool,
                self.get_parameter("protective_stop_topic").value,
                self._on_protective_stop,
                QoSProfile(
                    reliability=ReliabilityPolicy.RELIABLE,
                    durability=DurabilityPolicy.TRANSIENT_LOCAL,
                    history=HistoryPolicy.KEEP_LAST,
                    depth=1,
                ),
            )
        else:
            self.get_logger().warn(
                "보호정지 중계가 꺼져 있다 — 초음파 임계 진입에도 STOP_COMMAND 를 "
                "보내지 않는다. 센서 보드 없는 모터 단독 시험 구성이다"
            )

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

        # 핸드셰이크 여부와 무관하게 영원히 HELLO 를 보낸다(S15P11A301-321) -
        # 센서 브리지와 같은 패턴. 핸드셰이크 재시도와 링크 keepalive를 같은
        # 타이머로 겸하므로 별도 "재시도 vs 유지" 상태 분기가 필요 없다 - 무응답
        # 경고(S15P11A301-323)는 이 타이머가 도는 _send_hello() 안에서 낸다.
        keepalive_period = self.get_parameter("keepalive_period_s").value
        self._keepalive_timer = self.create_timer(keepalive_period, self._send_hello)
        # **보드가 죽어도 링크 상태를 계속 낸다** (S15P11A301-323). 종전에는 보드의
        # DIAGNOSTIC 프레임을 받았을 때만 발행해서, 보드가 무응답이면 /diagnostics 에
        # MOTOR 항목이 아예 없었다 — 「항목 없음」은 화면에서 「정상」과 구별되지 않는다.
        self._link_timer = self.create_timer(
            float(self.get_parameter("link_report_period_s").value), self._publish_link_status
        )

    def destroy_node(self) -> bool:
        self._transport.close()
        return super().destroy_node()

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence = (self._sequence + 1) & 0xFF
            return self._sequence

    def _send_frame(self, message_type: int, payload: bytes = b"") -> None:
        frame = build_motor_frame(message_type, self._next_sequence(), payload)
        try:
            self._transport.write_frame(frame)
        except Exception as exc:  # noqa: BLE001 - 포트가 아직 안 열렸을 수 있다
            # 끊긴 동안 DRIVE_COMMAND·HELLO 주기마다 쌓이므로 억제한다
            # (S15P11A301-264). 재연결 전이는 MotorSerialTransport 가 남긴다.
            self.get_logger().warning(
                f"프레임 전송 실패: {exc}", throttle_duration_sec=5.0
            )

    def _send_hello(self) -> None:
        self._hello_sent_count += 1
        self._send_frame(MSG_HELLO)
        # **한 번은 크게 말한다** (S15P11A301-323). 종전에는 HELLO 를 영원히 다시
        # 보내면서 로그가 조용했고, 증상은 관제의 MOTOR_BOARD_NO_ACK 하나뿐이었다.
        # keepalive 타이머가 핸드셰이크 재시도를 겸하므로(S15P11A301-321) 이
        # 경고도 별도 타이머 없이 여기서 낸다.
        if self._handshake_ok:
            return
        attempts = int(self.get_parameter("handshake_warn_after_attempts").value)
        if not self._handshake_warned and self._hello_sent_count >= attempts:
            self._handshake_warned = True
            port = self.get_parameter("port").value
            if self._rx_frame_count == 0:
                self.get_logger().error(
                    f"모터 ESP32 무응답: HELLO {self._hello_sent_count}회에 답이 없고 "
                    f"{port} 에서 프레임을 한 번도 받지 못했다. "
                    "보드 전원·펌웨어·USB 를 확인하라"
                )
            else:
                self.get_logger().error(
                    f"모터 ESP32 핸드셰이크 실패: 프레임 {self._rx_frame_count}건은 받았으나 "
                    f"HELLO_ACK 이 없다(해석 실패 {self._parse_error_count}건). "
                    "보레이트·프로토콜 버전·보드 역할을 확인하라"
                )

    def _publish_link_status(self) -> None:
        """링크 상태를 주기적으로 낸다. 판정은 `link_health` 가 갖는다 — 시험이 지킨다."""
        since = (
            None if self._last_rx_monotonic is None
            else time.monotonic() - self._last_rx_monotonic
        )
        verdict = motor_link_verdict(
            handshake_ok=self._handshake_ok,
            rx_frame_count=self._rx_frame_count,
            parse_error_count=self._parse_error_count,
            parse_errors_by_type=dict(self._parse_errors_by_type),
            hello_sent_count=self._hello_sent_count,
            since_last_rx_s=since,
        )
        status = DiagnosticStatus(
            level=_DIAGNOSTIC_LEVELS[verdict.level],
            name="esp32_bridge: MOTOR_LINK",
            message=verdict.message,
            hardware_id=str(self.get_parameter("port").value),
            values=[KeyValue(key=k, value=v) for k, v in verdict.values.items()],
        )
        self._diagnostics_pub.publish(
            build_array(self.get_clock().now().to_msg(), [status])
        )

    def _on_drive_command(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
            cmd = DriveCommand(
                mode=int(data["mode"]),
                flags=int(data.get("flags", 0)),
                target_drive_left_mmps=int(data["target_drive_left_mmps"]),
                target_drive_right_mmps=int(data["target_drive_right_mmps"]),
                target_steering_mdeg=int(
                    data.get("target_steering_mdeg", self._last_steering_mdeg)
                ),
                max_accel_mmps2=int(data.get("max_accel_mmps2", 0)),
                max_steering_rate_mdps=int(
                    data.get("max_steering_rate_mdps", self._last_steering_rate_mdps)
                ),
                command_timeout_ms=int(data.get("command_timeout_ms", 300)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.get_logger().warn(f"drive_command 파싱 실패, 무시함: {exc}")
            return
        self._last_steering_mdeg = cmd.target_steering_mdeg
        self._last_steering_rate_mdps = cmd.max_steering_rate_mdps
        self._send_frame(MSG_DRIVE_COMMAND, pack_drive_command(cmd))

    def _on_set_mode(self, msg: String) -> None:
        """`{"mode": "MANUAL"|"AUTO"}` → `SET_MODE` 프레임. 판단하지 않는다.

        **재전송하지 않는다.** 누름당 프레임 하나다. `STOP_COMMAND` 를 3회 보내는
        것은 멱등이기 때문이고 `SET_MODE` 는 아니다 - 3프레임이면 3ACK 이고 관제
        쪽에서는 3전이가 된다. 손실은 mission_manager 의 500ms 타임아웃과 운영자
        재시도가 덮는다.
        """
        try:
            data = json.loads(msg.data)
            requested = BOARD_MODE_VALUES[str(data["mode"])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self.get_logger().warn(f"set_mode 파싱 실패, 무시함: {exc}")
            return
        self._send_frame(MSG_SET_MODE, pack_set_mode(SetMode(requested, 0)))

    def _on_protective_stop(self, msg: Bool) -> None:
        now_s = self.get_clock().now().nanoseconds / 1e9
        decision = self._relay.observe(bool(msg.data), now_s)

        # 명세 03 메시지 표가 STOP_COMMAND 를 "즉시, 3회 반복 전송" 으로 정한다.
        # 상승에서 3회, 눌린 동안의 재확인은 1회다(protective_relay 참조).
        for _ in range(decision.repeat):
            self._send_frame(MSG_STOP_COMMAND)

        if decision.reason == 'RISING':
            self.get_logger().warn(
                "초음파 보호정지 진입 — STOP_COMMAND 중계 (명세 03-276)"
            )
        elif decision.reason == 'RELEASED':
            self.get_logger().info("초음파 보호정지 해제")
        elif decision.reason == 'REASSERT':
            self.get_logger().debug("보호정지 유지 — STOP_COMMAND 재확인")

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
            # **프레임이 선 것과 해석된 것을 따로 센다** (S15P11A301-323). 이 값이
            # 0 인가 아닌가가 「바이트가 안 온다」와 「보레이트가 어긋나 쓰레기가
            # 온다」를 가른다 — 종전에는 둘 다 침묵이라 구별할 수 없었다.
            self._rx_frame_count += 1
            self._last_rx_monotonic = time.monotonic()
            try:
                frame = parse_motor_frame(raw)
            except _FRAME_PARSE_ERRORS as error:
                # 실행하지는 않는다(§34-5). 다만 **버린 사실은 남긴다** — 카운터가
                # 없어서 진단이 한 바퀴 돌았다(S15P11A301-317).
                name = type(error).__name__
                self._parse_error_count += 1
                self._parse_errors_by_type[name] = self._parse_errors_by_type.get(name, 0) + 1
                self.get_logger().warning(
                    f"프레임 해석 실패({name}) — 누적 {self._parse_error_count}건. "
                    "보레이트·프로토콜 버전을 확인하라",
                    throttle_duration_sec=10.0,
                )
                continue

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
        if ack.protocol_version != MOTOR_PROTOCOL_VERSION:
            self.get_logger().error(
                f"프로토콜 버전 불일치: 보드={ack.protocol_version} Jetson={MOTOR_PROTOCOL_VERSION}"
            )
            return
        was_handshaked = self._handshake_ok
        self._handshake_ok = True
        if not was_handshaked:
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
                "acked_message_type_name": message_type_name(ack.acked_message_type),
                "acked_sequence": ack.acked_sequence,
                "result": ack.result,
                "result_name": ack_result_name(ack.result),
                "board_state": _state_name(ack.board_state),
            }
        )
        self._command_ack_pub.publish(msg)

        if ack.result != ACK_RESULT_ACCEPTED:
            self.get_logger().warn(
                f"모터 보드가 {message_type_name(ack.acked_message_type)} 를 거부했다: "
                f"{ack_result_name(ack.result)} (boardState={_state_name(ack.board_state)})",
                throttle_duration_sec=5.0,
            )

    def _handle_diagnostic(self, payload: bytes) -> None:
        diag = unpack_motor_diagnostic(payload)
        status = build_status(
            hardware_id=self.get_parameter("port").value,
            board_role=diag.board_role,
            board_state_name=_state_name(diag.board_state),
            fault_flags=diag.fault_flags,
            crc_error_count=diag.crc_error_count,
            dropped_frame_count=diag.dropped_frame_count,
            stale_sequence_count=diag.stale_sequence_count,
            # 링크가 죽었나(이 값이 큼) vs 상위가 DRIVE_COMMAND만 안 보내나(이 값은
            # 작은데 COMM_TIMEOUT_MOTOR fault는 섬)를 운영자가 /diagnostics 하나로
            # 가를 수 있게 한다(S15P11A301-321).
            extra_values=[KeyValue(key="link_silence_ms", value=str(diag.link_silence_ms))],
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

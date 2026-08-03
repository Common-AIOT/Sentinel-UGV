"""ROS와 관제 서버를 잇는 브리지 노드 (S15P11A301-128).

ROS 2 DDS를 인터넷 구간까지 확장하지 않는다. 관제에 필요한 데이터만 JSON으로
변환해 MQTT로 발행한다(명세 31-1).

발행 채널 (31-4).

    presence   QoS 1  Retain O   접속·종료·LWT
    state      QoS 1  Retain O   변경 시 + 1초 heartbeat
    telemetry  QoS 0  Retain X   2Hz
    events     QoS 1  Retain X   encounter·음성 보고 (S15P11A301-140·159)
    acks       QoS 1  Retain X   명령 처리 결과 (S15P11A301-143)

구독 채널 (31-4).

    cmd/mission  QoS 1  Retain 금지  관제 임무 제어 명령 (S15P11A301-143)

`cmd/drive`(수동 조종)는 아직 구독하지 않는다. 계약이 `MANUAL_DRIVE_COMMAND`로
따로이고 31-13 2단계이며, `MANUAL` 상태는 control session과 gamepad deadman이
필요하다(36장).

설계에서 중요한 세 가지.

**브로커가 없어도 죽지 않는다.** 관제 링크는 카메라·스트리밍·AI와 독립이어야
한다(32장 장애 격리). 연결이 없으면 발행을 조용히 버리고 계속 재시도한다.

**재연결 후 복구 순서를 지킨다.** 31-10이 presence → state → 임무 비교 →
Outbox → 영상 → telemetry 순서를 정했다. 순서가 뒤바뀌면 서버가 상태를 모르는
채로 telemetry를 먼저 받는다.

**재연결만으로 주행을 재개하지 않는다.** 31-4와 31-10이 명시한다. 이 노드는
상태만 알리고 주행 재개는 관제자의 명시적 명령으로만 일어난다.
"""

from __future__ import annotations

import json

import rclpy
import tf2_ros
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from nav_msgs.msg import Odometry
from sensor_msgs.msg import (
    CompressedImage,
    LaserScan,
    RelativeHumidity,
    Temperature,
)
from std_msgs.msg import String

from .command_relay import CommandRelay
from .message_mapper import (
    SAFETY_STATE_BY_MISSION_STATE,
    active_mission_id,
    environment_payload,
    MessageMapper,
    motion_payload,
    utc_now_iso,
    yaw_from_quaternion,
)
from .mqtt_client import CHANNEL_CMD_MISSION, MqttClient
from .outbox_repository import OutboxRepository
from .system_metrics import ComputeMetrics

# 센서가 살아 있다고 볼 최대 무소식 시간. 카메라는 30fps, 라이다는 약 11Hz이므로
# 2초는 넉넉하다. 너무 짧으면 순간 지연을 장애로 오판한다.
#
# 엔코더 오도메트리도 이 기준을 쓴다. ESP32가 50Hz로 보내므로
# (SENSOR_TASK_INTERVAL_MS = 20) 여유가 크다.
SENSOR_STALE_SECONDS = 2.0

# 온습도만 기준이 다르다 (S15P11A301-213).
#
# DHT11은 2초 주기다(펌웨어 DHT_INTERVAL_MS = 2000). SENSOR_STALE_SECONDS와
# **정확히 같으므로** 그 상수를 그대로 쓰면 시리얼 지연과 스케줄링 지터가 얹힐
# 때마다 경계를 넘나들어 온습도가 값과 null 사이에서 깜빡인다. 화면에서는
# 원인을 알 수 없는 깜빡임으로만 보인다.
#
# 주기의 3배로 둔다. 지터와 DHT 재시도(DHT_FAULT_STREAK_THRESHOLD = 3)를
# 견디면서, ESP32가 빠지면 6초 안에 null이 된다.
ENVIRONMENT_STALE_SECONDS = 6.0


class CloudBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__('cloud_bridge')

        self.declare_parameter('robot_id', 'SENTINEL-01')
        self.declare_parameter('broker_host', '127.0.0.1')
        self.declare_parameter('broker_port', 8883)
        self.declare_parameter('broker_username', '')
        self.declare_parameter('broker_password', '')
        # 접속 방식. S15P11A301-103에서 EC2 8883·1883이 막혀 있어 443
        # WebSocket으로 붙는다. tcp는 보안그룹이 열리면 쓸 수 있게 남겨 둔다.
        self.declare_parameter('broker_transport', 'websockets')
        self.declare_parameter('broker_ws_path', '/mqtt')
        # TLS 사용 여부와 CA 경로는 다른 파라미터다. 공인 인증서를 쓰는 운영에서는
        # tls_ca_certs가 비어 있어도 TLS를 켜야 한다(mqtt_client의 주석 참고).
        self.declare_parameter('tls_enabled', True)
        self.declare_parameter('tls_ca_certs', '')
        self.declare_parameter('tls_insecure', False)
        self.declare_parameter('keepalive_seconds', 30)
        # 명세 31-1은 MQTT 5다. 로컬 테스트 브로커(amqtt)가 5를 지원하지
        # 않으므로 검증할 때만 3으로 내린다. mqtt_client의 주석 참고.
        self.declare_parameter('protocol_version', 5)
        self.declare_parameter('state_period_seconds', 1.0)
        self.declare_parameter('telemetry_period_seconds', 0.5)
        self.declare_parameter('camera_topic', '/camera/image_raw/compressed')
        self.declare_parameter('scan_topic', '/scan')
        # ESP32 실측값 (S15P11A301-213). 기본값은 esp32_bridge.yaml의
        # 발행 토픽과 같아야 한다 — 다르면 구독이 조용히 비어 있고
        # environment/motion이 계속 null이라 미구현과 구분되지 않는다.
        self.declare_parameter('odometry_topic', '/wheel/odometry')
        self.declare_parameter('temperature_topic', '/environment/temperature')
        self.declare_parameter(
            'relative_humidity_topic', '/environment/relative_humidity'
        )
        # 탐지 노드 생존 판정용. 값을 소비하지 않고 도착 시각만 본다
        # (S15P11A301-192). encounter는 mission_manager가 만들어 보내므로
        # 이 노드가 후보를 해석할 이유는 없다.
        self.declare_parameter(
            'candidates_topic', '/perception/person_candidates'
        )
        # mission_manager가 발행하는 임무 상태(26.2). 이 노드는 읽기만 한다.
        self.declare_parameter('mission_status_topic', '/mission/status')
        # 관제 명령 경로 (S15P11A301-143). cmd/mission → 신호, 결과 → acks.
        self.declare_parameter('mission_signal_topic', '/mission/signal')
        self.declare_parameter(
            'command_result_topic', '/mission/command_result'
        )
        # SLAM이 만드는 프레임(S15P11A301-137). map → base_footprint 를 조회해
        # telemetry의 pose를 채운다. 명세 8.3의 TF 트리를 따른다.
        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('map_topic', '/map')
        # map_uploader가 임무 시작에 발급한 mapId(S15P11A301-193). 이 값이
        # 13.2 maps 행의 식별자이므로 telemetry가 자체 UUID를 만들지 않는다.
        self.declare_parameter(
            'map_registered_topic', '/map_uploader/registered'
        )
        # 사람 발견 이벤트를 관제로 중계한다(S15P11A301-140). mission_manager가
        # 발행하는 것을 그대로 31-5 봉투에 담아 MQTT events 채널로 보낸다.
        self.declare_parameter('encounter_topic', '/perception/encounter')
        # 음성 세션이 구조화한 보고. encounter와 마찬가지로 중요한 이벤트라
        # MQTT가 끊기면 Outbox에 보관한다(S15P11A301-159).
        self.declare_parameter('interaction_report_topic', '/interaction/report')
        # 한 번에 재전송할 Outbox 항목 수. 재연결 직후 수백 건을 몰아 보내면
        # Wi-Fi를 점유해 관제 영상이 밀린다(32장 우선순위).
        self.declare_parameter('outbox_batch_size', 20)
        # 보관분을 흘려보내는 주기. 재연결 시 한 배치만 보내므로 그보다 많이
        # 쌓였을 때 남은 것을 이 타이머가 이어서 보낸다.
        self.declare_parameter('outbox_flush_period_seconds', 5.0)
        self.declare_parameter(
            'outbox_path', '/var/lib/sentinel/bridge/outbox.sqlite3'
        )

        robot_id = self._param('robot_id')
        self.mapper = MessageMapper(robot_id)
        self.metrics = ComputeMetrics()

        # Outbox는 이 티켓에서 뼈대만 둔다. 실제 적재는 S15P11A301-123이다.
        # 경로를 쓸 수 없으면 경고만 남기고 계속한다. 이벤트 발행이 아직 없으므로
        # 이것이 브리지를 막을 이유가 없다.
        self.outbox: OutboxRepository | None = None
        try:
            self.outbox = OutboxRepository(self._param('outbox_path'))
        except OSError as error:
            self.get_logger().warn(
                f"Outbox를 열 수 없다({error}). 이벤트 보관 없이 계속한다. "
                "S15P11A301-123 전에는 발행할 이벤트가 없다."
            )

        # 센서 생존 판정용. 마지막 수신 시각만 들고 있는다. 메시지 내용은 쓰지
        # 않으므로 콜백을 가볍게 유지한다.
        self._camera_last_seen: float | None = None
        self._scan_last_seen: float | None = None
        self._candidates_last_seen: float | None = None

        # ESP32 실측값 (S15P11A301-213). 여기는 생존이 아니라 값을 쓰므로
        # 마지막 값과 수신 시각을 함께 들고 있는다.
        #
        # **오래된 값은 유지하지 않고 null로 보낸다.** 마지막 값을 계속 보내면
        # ESP32를 뽑아도 관제 화면의 온습도가 그대로 남아, 죽은 센서를 살아
        # 있는 것으로 보여준다. null과 값을 구분하는 것이 이 스키마의 규약이다.
        self._temperature_c: float | None = None
        self._temperature_last_seen: float | None = None
        self._humidity_ratio: float | None = None
        self._humidity_last_seen: float | None = None
        self._linear_mps: float | None = None
        self._angular_radps: float | None = None
        self._odometry_last_seen: float | None = None
        # 마지막으로 받은 임무 상태. mission_manager가 상태 변경 시에만 발행하므로
        # 여기 들고 있다가 1초 heartbeat마다 관제로 내보낸다(31-4).
        self._mission_status: dict | None = None
        # 지도 세션 식별자. **map_uploader가 발급한 값을 받아 쓴다**
        # (S15P11A301-193). 이전에는 이 노드가 uuid4를 만들었는데, 그 값은
        # 백엔드 maps.id와 무관해서 관제가 두 식별자를 보게 됐다.
        #
        # 아직 못 받았으면 None이다. 지어내지 않는다 — 스키마가 null을 허용하고,
        # 틀린 식별자를 보내면 관제가 다른 지도의 좌표로 해석한다.
        self._map_id: str | None = None
        # pose 조회 실패를 매 주기 로그로 남기면 2Hz로 쏟아진다. 상태가 바뀔 때만
        # 남긴다.
        self._pose_available = False

        # BEST_EFFORT로 구독한다. RELIABLE 구독자는 BEST_EFFORT 발행자와 호환되지
        # 않아 메시지를 하나도 받지 못하지만, 그 반대는 문제가 없다.
        #
        # usb_cam은 RELIABLE로 발행하고(S15P11A301-62) ydlidar는 BEST_EFFORT로
        # 발행한다. 처음에 카메라 기준으로 RELIABLE을 썼다가 라이다에서
        # "incompatible QoS. No messages will be received" 경고를 받았다.
        #
        # 여기서는 메시지 내용이 아니라 생존만 보므로 유실을 감당할 수 있다.
        # 스트리밍 경로처럼 프레임 무결성이 필요한 곳과는 요구가 다르다.
        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            CompressedImage, self._param('camera_topic'),
            self._on_camera, sensor_qos,
        )
        self.create_subscription(
            LaserScan, self._param('scan_topic'), self._on_scan, sensor_qos
        )
        # 탐지 노드 생존 (S15P11A301-192).
        #
        # 후보 토픽은 사람이 없어도 주기적으로 발행되므로(0.2초) heartbeat로 쓸 수
        # 있다. 이 구독을 두는 이유는 탐지 노드가 죽어도 스택 나머지가 정상
        # 기동하기 때문이다 — 화면상으로는 정상이고 로그를 뒤져야 안다.
        # 실제로 그 상태로 여러 검증을 돌린 뒤에야 알아챘다.
        self.create_subscription(
            String, self._param('candidates_topic'),
            self._on_candidates, sensor_qos,
        )

        # ESP32 실측값 (S15P11A301-213).
        #
        # 발행 쪽(esp32_sensor_bridge_node)이 RELIABLE이므로 맞춘다. BEST_EFFORT
        # 구독자도 RELIABLE 발행자를 받을 수 있지만, 여기는 생존이 아니라 값을
        # 쓰는 구독이라 유실을 감당할 이유가 없다. 온습도는 2초에 한 번뿐이어서
        # 한 번 잃으면 그 값이 6초 동안 없는 것과 같다.
        esp32_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.create_subscription(
            Odometry, self._param('odometry_topic'), self._on_odometry, esp32_qos
        )
        self.create_subscription(
            Temperature, self._param('temperature_topic'),
            self._on_temperature, esp32_qos,
        )
        self.create_subscription(
            RelativeHumidity, self._param('relative_humidity_topic'),
            self._on_relative_humidity, esp32_qos,
        )

        # 임무 상태는 TRANSIENT_LOCAL로 구독한다. mission_manager가 같은 설정으로
        # 발행하므로, 이 노드가 나중에 떠도 마지막 상태를 즉시 받는다. VOLATILE로
        # 구독하면 다음 전이까지 관제에 "임무 상태 모름"을 계속 보내게 된다.
        self.mission_status_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.mission_status_sub = self.create_subscription(
            String,
            self._param('mission_status_topic'),
            self._on_mission_status,
            self.mission_status_qos,
        )

        # map_uploader의 mapId. TRANSIENT_LOCAL로 발행되므로 이 노드가 나중에
        # 떠도 현재 임무의 값을 즉시 받는다(S15P11A301-193).
        self.create_subscription(
            String,
            self._param('map_registered_topic'),
            self._on_map_registered,
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )

        # TF는 map → base_footprint 조회에만 쓴다. SLAM(S15P11A301-137)이 없으면
        # 조회가 실패하고 pose는 null이 된다. 그것이 정확한 표현이다.
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # 관제 명령을 신호로 바꿔 넣는 발행자 (S15P11A301-143).
        #
        # RELIABLE이어야 한다. 명령을 잃으면 조작자가 버튼을 눌렀는데 아무 일도
        # 일어나지 않고, ACK도 오지 않아 원인을 알 수 없다.
        command_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.signal_pub = self.create_publisher(
            String, self._param('mission_signal_topic'), command_qos
        )
        self.create_subscription(
            String,
            self._param('command_result_topic'),
            self._on_command_result,
            command_qos,
        )

        # 명령 판단과 ACK 기억은 CommandRelay가 맡는다. ROS를 모르는 클래스라
        # CI에서 시험할 수 있다.
        self.relay = CommandRelay()

        # encounter는 잃으면 사람을 발견한 사실이 사라진다. RELIABLE로 구독한다.
        # mission_manager가 같은 설정으로 발행한다(S15P11A301-133).
        self.create_subscription(
            String,
            self._param('encounter_topic'),
            self._on_encounter,
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10,
            ),
        )
        # 음성 노드가 bridge보다 먼저 보고를 발행해도 마지막 보고를 받도록
        # TRANSIENT_LOCAL을 맞춘다. interactionId/messageId가 중복 저장을 막는다.
        self.create_subscription(
            String,
            self._param('interaction_report_topic'),
            self._on_interaction_report,
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=10,
            ),
        )

        self.mqtt = MqttClient(
            robot_id,
            self._param('broker_host'),
            int(self._param('broker_port')),
            username=self._param('broker_username') or None,
            password=self._param('broker_password') or None,
            tls_enabled=bool(self._param('tls_enabled')),
            tls_ca_certs=self._param('tls_ca_certs') or None,
            tls_insecure=bool(self._param('tls_insecure')),
            transport=str(self._param('broker_transport')),
            ws_path=str(self._param('broker_ws_path')),
            keepalive=int(self._param('keepalive_seconds')),
            protocol_version=int(self._param('protocol_version')),
            on_connected=self._on_broker_connected,
            logger=self.get_logger(),
        )
        # 구독은 start() 뒤에 등록해도 된다. 접속 전이면 보관만 하고
        # `_handle_connect`가 실제 구독을 걸며, 재연결 때마다 다시 건다.
        self.mqtt.subscribe(CHANNEL_CMD_MISSION, self._on_mission_command)
        self.mqtt.start()

        self.create_timer(float(self._param('state_period_seconds')), self._publish_state)
        self.create_timer(
            float(self._param('outbox_flush_period_seconds')), self._on_outbox_tick
        )
        self.create_timer(
            float(self._param('telemetry_period_seconds')), self._publish_telemetry
        )

        self.get_logger().info(
            f"cloud_bridge 시작. robotId={robot_id} "
            f"broker={self._param('broker_host')}:{self._param('broker_port')}"
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    # ------------------------------------------------------------------
    # 센서 생존
    # ------------------------------------------------------------------

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _on_camera(self, _message: CompressedImage) -> None:
        self._camera_last_seen = self._now()

    def _on_scan(self, _message: LaserScan) -> None:
        self._scan_last_seen = self._now()

    def _on_odometry(self, message: Odometry) -> None:
        """엔코더 오도메트리 (S15P11A301-213).

        자세는 여기서 쓰지 않는다. pose는 SLAM의 map→base_footprint TF에서
        가져오며(그쪽이 누적 오차를 보정한다), 이 토픽에서는 속도만 쓴다.
        """
        self._linear_mps = message.twist.twist.linear.x
        self._angular_radps = message.twist.twist.angular.z
        self._odometry_last_seen = self._now()

    def _on_temperature(self, message: Temperature) -> None:
        self._temperature_c = message.temperature
        self._temperature_last_seen = self._now()

    def _on_relative_humidity(self, message: RelativeHumidity) -> None:
        # ROS 규약대로 0~1 비율이다. 퍼센트 변환은 environment_payload가 한다.
        self._humidity_ratio = message.relative_humidity
        self._humidity_last_seen = self._now()

    def _on_map_registered(self, message: String) -> None:
        """map_uploader가 발급한 mapId를 받아 둔다."""
        try:
            body = json.loads(message.data)
        except json.JSONDecodeError:
            return
        if not isinstance(body, dict):
            return
        map_id = body.get('mapId')
        if not map_id or map_id == self._map_id:
            return
        self._map_id = str(map_id)
        self.get_logger().info(
            f'지도 세션 수신. mapId={self._map_id} '
            f'mission={str(body.get("missionId") or "")[:8]}'
        )

    def _on_mission_status(self, message: String) -> None:
        """mission_manager의 임무 상태를 받아 둔다.

        상태 변경 시에만 오므로 여기 보관하고 1초 heartbeat마다 내보낸다(31-4).
        """
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'임무 상태 JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict) or 'state' not in payload:
            self.get_logger().warn(
                '임무 상태 본문에 state가 없다. mission-status.schema.json을 확인한다.'
            )
            return

        previous = (self._mission_status or {}).get('state')
        self._mission_status = payload
        if payload['state'] != previous:
            self.get_logger().info(
                f"임무 상태 수신: {previous or '(없음)'} → {payload['state']}"
            )

    # ------------------------------------------------------------------
    # 관제 명령 (S15P11A301-143, 31-4·31-6·27.4)
    # ------------------------------------------------------------------

    def _on_mission_command(self, envelope: dict) -> None:
        """`cmd/mission`으로 온 명령을 `/mission/signal`의 신호로 바꿔 넣는다.

        판단은 `CommandRelay`가 한다. 이 메서드는 배선만 한다 — 결정을 여기 두면
        rclpy 때문에 CI에서 시험할 수 없고, 중복 명령 처리는 실물로 재현할 수
        없는 경로다(command_relay 모듈 문서 참고).

        이 메서드는 **paho의 네트워크 스레드에서 돈다.** rclpy 발행은 스레드에서
        불러도 되지만 예외를 흘리면 그 스레드가 죽는다.
        `mqtt_client._handle_message`가 감싸 두었다.
        """
        decision = self.relay.decide(
            envelope, mission_manager_alive=self._mission_manager_alive()
        )

        if decision.ack is not None:
            if decision.replayed:
                self.get_logger().info(decision.note)
            else:
                self.get_logger().warn(decision.note)
            self._publish_ack(decision.ack, decision.mission_id)
            return

        if decision.signal is None:
            # 회신할 수 없거나 처리 중인 중복이다. 조용히 넘기지 않는다.
            self.get_logger().warn(decision.note)
            return

        body = {
            'signal': decision.signal,
            'sentAt': utc_now_iso(),
            'source': 'CONTROL',
            'encounterId': None,
            'missionId': decision.mission_id,
            'commandId': decision.command_id,
            'detail': f'관제 {decision.command_type}',
        }
        self.signal_pub.publish(String(data=json.dumps(body, ensure_ascii=False)))
        self.get_logger().info(
            f'관제 명령 {decision.note} '
            f'(commandId={(decision.command_id or "")[:8]})'
        )

    def _on_command_result(self, message: String) -> None:
        """`mission_manager`의 처리 결과를 `acks`로 회신한다.

        본문을 그대로 넘긴다. 계약이 `command-ack.schema.json`이며 상태 머신이
        이미 그 형식으로 만들었다. 수락·거부를 판단하는 것은 상태 머신이고 bridge가
        다시 판단하면 두 곳이 어긋난다(26.1 단일 권한).
        """
        try:
            body = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'명령 결과 JSON 해석 실패: {error}')
            return
        if not isinstance(body, dict):
            self.get_logger().warn('명령 결과 본문이 객체가 아니다')
            return

        command_id = self.relay.resolve(body)
        if command_id is None:
            self.get_logger().warn('commandId 없는 명령 결과를 버렸다')
            return

        # 이 시점에는 명령이 온 봉투의 missionId를 알 수 없다. 백엔드는 commandId로
        # control_commands 행을 찾으므로 없어도 동작한다.
        self._publish_ack(body, None)

    def _publish_ack(self, body: dict, mission_id: str | None) -> None:
        """ACK를 발행한다. 브로커가 없으면 Outbox에 보관한다.

        ACK를 버리면 안 된다. 관제의 `control_commands.result`가 영원히 PENDING으로
        남아 조작자가 명령이 먹혔는지 알 수 없다. 31-10이 "중요 이벤트"를 Outbox에
        보관하라고 한 범위에 든다 — encounter와 같은 취급이다.
        """
        envelope = self.mapper.command_ack(body, mission_id=mission_id)
        if self.mqtt.publish('acks', envelope):
            return
        if self.outbox is None:
            self.get_logger().error(
                f"브로커도 Outbox도 없어 ACK를 잃었다: {body.get('commandId')}"
            )
            return
        self.outbox.enqueue(envelope, 'acks')
        self.get_logger().warn(
            f"브로커 없음. ACK를 Outbox에 보관했다 (대기 {self.outbox.count()}건)"
        )

    def _slam_alive(self) -> bool:
        """SLAM이 떠 있는가.

        `/map` 발행자 수를 본다. TF 조회만으로는 판단할 수 없다 — `tf2_ros.Buffer`가
        마지막 변환을 얼마간 들고 있어서, SLAM이 죽은 직후에도 조회가 성공한다.
        그 값을 관제로 보내면 로봇이 멈춘 뒤에도 옛 위치가 계속 보인다.

        S15P11A301-135의 mission_manager 생존 판정과 같은 방식이다.
        """
        return self.count_publishers(self._param('map_topic')) > 0

    def _pose(self) -> dict | None:
        """map → base_footprint 를 telemetry의 pose로 바꾼다 (명세 8.3·23.2).

        SLAM이 없으면 None이다. 값을 지어내지 않는다. 관제가 "위치 모름"과
        "원점에 있음"을 구별해야 하고, 후자로 오해하면 지도에 로봇이 엉뚱한 곳에
        그려진다.
        """
        if not self._slam_alive():
            if self._pose_available:
                self.get_logger().warn('SLAM이 내려갔다. pose를 null로 보낸다.')
            self._pose_available = False
            # mapId는 버리지 않는다. 이제 map_uploader가 임무 단위로 발급하고
            # 백엔드 maps.id가 되므로(S15P11A301-193), SLAM 재기동으로 값을
            # 바꾸면 같은 임무에 식별자가 둘 생긴다. 백엔드도 임무당 지도
            # 하나를 전제한다(maps.mission_id 조회가 LIMIT 1이다).
            return None

        try:
            transform = self.tf_buffer.lookup_transform(
                str(self._param('map_frame')),
                str(self._param('base_frame')),
                rclpy.time.Time(),
            )
        except (
            tf2_ros.LookupException,
            tf2_ros.ConnectivityException,
            tf2_ros.ExtrapolationException,
        ) as error:
            if self._pose_available:
                self.get_logger().warn(f'pose 조회 실패: {type(error).__name__}')
            self._pose_available = False
            return None

        if not self._pose_available:
            self.get_logger().info('pose 조회 성공. telemetry에 위치를 담는다.')
        self._pose_available = True

        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return {
            'x': round(translation.x, 3),
            'y': round(translation.y, 3),
            'yaw': round(
                yaw_from_quaternion(rotation.x, rotation.y, rotation.z, rotation.w), 4
            ),
            'mapId': self._map_id,
        }

    def _mission_manager_alive(self) -> bool:
        """mission_manager가 떠 있는가.

        보관한 상태를 계속 보내면 안 되기 때문에 확인한다. state 채널은 Retain이라
        관제가 마지막 값을 계속 보게 되는데, 노드가 죽은 뒤에도 EXPLORING이 남으면
        운영자가 로봇이 탐사 중이라고 믿는다. 안전 문제다.

        발행자 수를 쓴다. 이 토픽은 상태 변경 시에만 발행되므로 마지막 수신 시각으로
        생존을 판단할 수 없다. 전이가 몇 분에 한 번일 수 있다.
        """
        return self.count_publishers(self._param('mission_status_topic')) > 0

    def _fresh(
        self, last_seen: float | None, stale_after: float = SENSOR_STALE_SECONDS
    ) -> bool | None:
        """None은 "한 번도 못 받음"이고 False는 "받다가 끊김"이다.

        관제 화면이 "미구현·미연결"과 "장애"를 구분해야 하므로 섞지 않는다.

        `stale_after`를 인자로 둔 이유는 온습도만 기준이 다르기 때문이다.
        ENVIRONMENT_STALE_SECONDS의 주석 참고 (S15P11A301-213).
        """
        if last_seen is None:
            return None
        return (self._now() - last_seen) <= stale_after

    def _health(self) -> dict[str, bool | None]:
        return {
            # 엔코더 토픽 신선도로 판단한다 (S15P11A301-213). 시리얼이 끊기면
            # esp32_sensor_bridge가 발행을 멈추므로 이것이 USB 연결 상태다.
            # 한 번도 못 받았으면 None이고, 그것은 "확인할 수단이 없다"와 같다 —
            # ESP32가 아예 안 붙은 구성이 그렇다.
            'mcuConnected': self._fresh(self._odometry_last_seen),
            'lidarOk': self._fresh(self._scan_last_seen),
            'cameraOk': self._fresh(self._camera_last_seen),
        }

    def _environment(self) -> dict[str, float] | None:
        """DHT11 온습도. 오래되면 null이다 (S15P11A301-213)."""
        if not self._fresh(self._temperature_last_seen, ENVIRONMENT_STALE_SECONDS):
            return None
        if not self._fresh(self._humidity_last_seen, ENVIRONMENT_STALE_SECONDS):
            return None
        return environment_payload(self._temperature_c, self._humidity_ratio)

    def _motion(self) -> dict[str, float] | None:
        """엔코더 실측 속도. 오래되면 null이다 (S15P11A301-213)."""
        if not self._fresh(self._odometry_last_seen):
            return None
        return motion_payload(self._linear_mps, self._angular_radps)

    # ------------------------------------------------------------------
    # 발행
    # ------------------------------------------------------------------

    def _on_encounter(self, message: String) -> None:
        """사람 발견 이벤트를 관제로 중계한다 (S15P11A301-140).

        본문을 조립하지 않고 그대로 넘긴다. `mission_manager`가 이미
        `encounter.schema.json` 형식으로 만들었고, 여기서 다시 만들면 두 곳이
        어긋난다.

        `missionId`가 없으면 발행하지 않는다. 백엔드가 `encounters.mission_id`를
        NOT NULL FK로 두고 임무 없는 encounter를 적재하지 않으므로
        (S15P11A301-138), 보내도 버려진다. 그런 것을 Outbox에 쌓으면 재전송이
        영원히 실패하며 디스크만 먹는다.
        """
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'encounter JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict) or 'phase' not in payload:
            self.get_logger().warn(
                'encounter 본문에 phase가 없다. encounter.schema.json을 확인한다.'
            )
            return

        phase = payload.get('phase')
        if not payload.get('missionId'):
            self.get_logger().warn(
                f'{phase}를 관제로 보내지 않는다. missionId가 없어 백엔드가 '
                '적재하지 않는다. 녹화는 영향받지 않는다.'
            )
            return

        envelope = self.mapper.encounter(payload)
        if self.mqtt.publish('events', envelope):
            self.get_logger().info(
                f'events 발행 {phase} {str(payload.get("encounterId"))[:8]}'
            )
            return

        # 브로커가 없다. 31-10이 "중요 이벤트는 Outbox에 보관한 뒤 연결 복구 후
        # 재전송한다"고 정했다. 재난 현장에서 Wi-Fi가 끊기는 것이 전제이므로
        # 그 동안 발견한 사람이 기록에서 사라지면 안 된다.
        if self.outbox is None:
            self.get_logger().error(
                f'{phase}를 보낼 수도 보관할 수도 없다. Outbox가 열리지 않았다. '
                '이 이벤트는 관제에 기록되지 않는다.'
            )
            return
        try:
            self.outbox.enqueue(envelope, 'events')
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f'Outbox 보관 실패: {error}')
            return
        self.get_logger().warn(
            f'브로커 없음. {phase}를 Outbox에 보관했다 (대기 {self.outbox.count()}건)'
        )

    def _on_interaction_report(self, message: String) -> None:
        """음성 보고를 events 채널로 발행하고 단절 중에는 Outbox에 보관한다."""

        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'음성 보고 JSON 해석 실패: {error}')
            return
        required = {'interactionId', 'encounterId', 'missionId', 'sessionReport'}
        if not isinstance(payload, dict) or not required.issubset(payload):
            self.get_logger().warn(
                '음성 보고 필수 필드가 없다. interaction-report.schema.json을 확인한다.'
            )
            return
        if not payload.get('missionId'):
            self.get_logger().warn(
                'missionId 없는 음성 보고를 관제로 보내지 않는다.'
            )
            return

        envelope = self.mapper.interaction_report(payload)
        if self.mqtt.publish('events', envelope):
            self.get_logger().info(
                '음성 보고 events 발행 '
                f'{str(payload.get("interactionId"))[:8]}'
            )
            return
        if self.outbox is None:
            self.get_logger().error(
                '음성 보고를 보낼 수도 보관할 수도 없다. Outbox가 열리지 않았다.'
            )
            return
        try:
            self.outbox.enqueue(envelope, 'events')
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f'음성 보고 Outbox 보관 실패: {error}')
            return
        self.get_logger().warn(
            '브로커 없음. 음성 보고를 Outbox에 보관했다 '
            f'(대기 {self.outbox.count()}건)'
        )

    def _flush_outbox(self) -> int:
        """31-10 복구 순서 4번. 보관한 이벤트를 재전송한다.

        한 번에 배치 크기만큼만 보낸다. 재연결 직후 수백 건을 몰아 보내면 Wi-Fi를
        점유해 관제 영상이 밀린다(32장 우선순위). 남은 것은 다음 재연결이나
        타이머가 이어서 보낸다.
        """
        if self.outbox is None:
            return 0
        sent = 0
        try:
            items = self.outbox.pending(limit=int(self._param('outbox_batch_size')))
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f'Outbox 조회 실패: {error}')
            return 0

        for message_id, channel, payload in items:
            if not self.mqtt.publish(channel, payload):
                # 다시 끊겼다. 남은 것은 그대로 두고 다음 기회에 보낸다.
                break
            try:
                self.outbox.mark_sent(message_id)
            except Exception as error:  # noqa: BLE001
                self.get_logger().error(f'Outbox 표시 실패: {error}')
                break
            sent += 1
        return sent

    def _on_outbox_tick(self) -> None:
        """보관분이 남아 있으면 조금씩 흘려보낸다.

        재연결 콜백이 한 배치만 보내므로 그보다 많이 쌓였을 때 이 타이머가 이어
        보낸다. 브로커가 없으면 `_flush_outbox`가 첫 발행에서 멈추므로 여기서
        연결 여부를 다시 확인하지 않는다.
        """
        if self.outbox is None or not self.mqtt.connected:
            return
        if self.outbox.count() == 0:
            return
        sent = self._flush_outbox()
        if sent:
            self.get_logger().info(
                f'Outbox {sent}건 재전송 (남은 {self.outbox.count()}건)'
            )

    def _on_broker_connected(self) -> None:
        """31-10 복구 순서. paho 콜백 스레드에서 호출된다.

        presence → state → 임무 비교 → Outbox → 영상 → telemetry 순서다. 3번(임무
        비교)과 5번(영상 업로드 재개)은 해당 기능이 아직 없으므로 자리만 남긴다.
        영상 업로드는 `media_uploader`가 별 프로세스로 알아서 재시도한다.
        6번 telemetry는 타이머가 재개한다.
        """
        self.mqtt.publish('presence', self.mapper.presence_online())
        self._publish_state()
        sent = self._flush_outbox()
        summary = 'presence, state 발행'
        if sent:
            summary += f', Outbox {sent}건 재전송'
        if self.outbox is not None and self.outbox.count():
            summary += f' (남은 {self.outbox.count()}건은 다음 기회에)'
        self.get_logger().info(f'복구 순서 완료: {summary}')

    def _publish_state(self) -> None:
        if not self.mqtt.connected:
            return
        alive = self._mission_manager_alive()
        status = self._mission_status if alive else None

        if status is not None:
            mission_state = status.get('state')
            # 26.2 상태를 31-5의 safetyState enum으로 옮긴다. 매핑에 없는 상태는
            # 조용히 넘기지 않는다. RUNNING으로 기본값을 주면 정지해야 하는 상태가
            # 관제에 주행 중으로 보인다.
            safety_state = SAFETY_STATE_BY_MISSION_STATE.get(mission_state)
            if safety_state is None:
                self.get_logger().warn(
                    f'모르는 임무 상태 "{mission_state}". '
                    'SAFETY_STATE_BY_MISSION_STATE에 추가한다. '
                    '안전을 위해 STOPPED로 보낸다.'
                )
                safety_state = 'STOPPED'
            control_mode = 'MANUAL' if mission_state == 'MANUAL' else 'AUTO'
        else:
            # mission_manager가 없으면 임무 상태를 모른다. 값을 지어내지 않고
            # null로 두어 관제가 "모름"을 알 수 있게 한다. safetyState는 안전
            # 기본값을 쓴다. 주행을 지시하는 노드가 없으므로 실제로 멈춰 있다.
            mission_state = None
            safety_state = 'SAFE_IDLE'
            control_mode = None

        message = self.mapper.state(
            mission_state=mission_state,
            control_mode=control_mode,
            safety_state=safety_state,
            # 활성 임무일 때만 채운다(S15P11A301-190). 여기가 None으로 박혀
            # 있어서 관제가 "지금 어떤 임무를 하고 있나"를 state만으로는 알 수
            # 없었다.
            active_mission_id=active_mission_id(status),
            components={
                'camera': bool(self._fresh(self._camera_last_seen)),
                'lidar': bool(self._fresh(self._scan_last_seen)),
                # 탐지 노드가 죽어도 스택 나머지는 정상 기동한다. 이 값이 없으면
                # 관제 화면상 정상으로 보인다(S15P11A301-192).
                'detector': bool(self._fresh(self._candidates_last_seen)),
                # ESP32 시리얼 (S15P11A301-213). 이것이 false면 온습도·속도가
                # null인 이유가 설명된다 — 값이 없는 것과 보드가 빠진 것을
                # 관제가 구분할 수 있어야 한다.
                'mcu': bool(self._fresh(self._odometry_last_seen)),
                # 관제가 "임무 상태가 왜 비어 있나"를 구분할 수 있게 한다.
                'missionManager': alive,
            },
        )
        self.mqtt.publish('state', message)

    def _publish_telemetry(self) -> None:
        if not self.mqtt.connected:
            return
        # 봉투의 missionId가 백엔드에서 robot_pose.mission_id가 된다
        # (S15P11A301-190). 이 값이 비면 그 컬럼이 null인 행이 쌓이고,
        # missionId로 조회하는 관제 API가 아무것도 못 찾는다.
        #
        # 젯슨 쪽에는 아무 징후도 없다. 발행은 성공하고 적재도 성공하며 서버
        # 오류도 나지 않는다. 임무 5개에서 telemetry가 0건인 것을 관제 API로
        # 조회해서야 알았다.
        mission_id = active_mission_id(
            self._mission_status if self._mission_manager_alive() else None
        )
        message = self.mapper.telemetry(
            pose=self._pose(),
            # ESP32 실측값 (S15P11A301-213). 값이 오래되면 각 함수가 null을
            # 돌려주므로 마지막 값이 남지 않는다.
            motion=self._motion(),
            # battery는 계속 null이다. S15P11A301-174에 전압 계측이 없고
            # FAULT_UNDERVOLTAGE 플래그만 있다. 없는 값을 0이나 100으로 채우면
            # 관제가 잔량을 안다고 믿게 된다.
            battery=None,
            environment=self._environment(),
            compute=self.metrics.sample(),
            health=self._health(),
            mission_state=(
                (self._mission_status or {}).get('state')
                if self._mission_manager_alive()
                else None
            ),
            mission_id=mission_id,
        )
        self.mqtt.publish('telemetry', message)

    # ------------------------------------------------------------------
    # 종료
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """정상 종료. OFFLINE(SHUTDOWN)을 직접 보낸 뒤 DISCONNECT한다.

        DISCONNECT를 보내면 브로커가 LWT를 발행하지 않는다. 그래서 관제에서
        정상 종료(SHUTDOWN)와 비정상 종료(MQTT_CONNECTION_LOST)가 구분된다.
        """
        if self.mqtt.connected:
            self.mqtt.publish('presence', self.mapper.presence_offline_shutdown())
        self.mqtt.stop()
        if self.outbox is not None:
            self.outbox.close()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CloudBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

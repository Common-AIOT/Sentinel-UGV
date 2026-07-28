"""ROS와 관제 서버를 잇는 브리지 노드 (S15P11A301-128).

ROS 2 DDS를 인터넷 구간까지 확장하지 않는다. 관제에 필요한 데이터만 JSON으로
변환해 MQTT로 발행한다(명세 31-1).

발행 채널은 셋이다(31-4).

    presence   QoS 1  Retain O   접속·종료·LWT
    state      QoS 1  Retain O   변경 시 + 1초 heartbeat
    telemetry  QoS 0  Retain X   2Hz

`events`와 `acks`는 이 티켓 범위가 아니다. `events`는 S15P11A301-123,
`cmd/*` 구독과 `acks`는 ESP32 연동 이후다.

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

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import CompressedImage, LaserScan

from .message_mapper import MessageMapper
from .mqtt_client import MqttPublisher
from .outbox_repository import OutboxRepository
from .system_metrics import ComputeMetrics

# 센서가 살아 있다고 볼 최대 무소식 시간. 카메라는 30fps, 라이다는 약 11Hz이므로
# 2초는 넉넉하다. 너무 짧으면 순간 지연을 장애로 오판한다.
SENSOR_STALE_SECONDS = 2.0


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

        self.mqtt = MqttPublisher(
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
        self.mqtt.start()

        self.create_timer(float(self._param('state_period_seconds')), self._publish_state)
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

    def _fresh(self, last_seen: float | None) -> bool | None:
        """None은 "한 번도 못 받음"이고 False는 "받다가 끊김"이다.

        관제 화면이 "미구현·미연결"과 "장애"를 구분해야 하므로 섞지 않는다.
        """
        if last_seen is None:
            return None
        return (self._now() - last_seen) <= SENSOR_STALE_SECONDS

    def _health(self) -> dict[str, bool | None]:
        return {
            # ESP32 연동(S15P11A301-84~86) 전에는 확인할 수단이 없다.
            'mcuConnected': None,
            'lidarOk': self._fresh(self._scan_last_seen),
            'cameraOk': self._fresh(self._camera_last_seen),
        }

    # ------------------------------------------------------------------
    # 발행
    # ------------------------------------------------------------------

    def _on_broker_connected(self) -> None:
        """31-10 복구 순서. paho 콜백 스레드에서 호출된다.

        3~5번(임무 비교, Outbox 재전송, 영상 업로드 재개)은 해당 기능이 아직
        없으므로 자리만 남긴다. 6번 telemetry는 타이머가 알아서 재개한다.
        """
        self.mqtt.publish('presence', self.mapper.presence_online())
        self._publish_state()
        self.get_logger().info('복구 순서 완료: presence, state 발행')

    def _publish_state(self) -> None:
        if not self.mqtt.connected:
            return
        # 임무 상태 머신(14.1, 26.2)이 아직 없으므로 안전 기본값을 보낸다.
        # 값을 지어내지 않고 null로 두어 관제가 "모름"을 알 수 있게 한다.
        message = self.mapper.state(
            mission_state=None,
            control_mode=None,
            safety_state='SAFE_IDLE',
            active_mission_id=None,
            components={
                'camera': bool(self._fresh(self._camera_last_seen)),
                'lidar': bool(self._fresh(self._scan_last_seen)),
            },
        )
        self.mqtt.publish('state', message)

    def _publish_telemetry(self) -> None:
        if not self.mqtt.connected:
            return
        message = self.mapper.telemetry(
            # SLAM·엔코더·ESP32가 붙기 전에는 null이다. 31-6 전체 형태를 유지해
            # 나중에 필드를 추가하지 않도록 한다.
            pose=None,
            motion=None,
            battery=None,
            environment=None,
            compute=self.metrics.sample(),
            health=self._health(),
            mission_state=None,
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

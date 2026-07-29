"""MQTT 발행 담당 (S15P11A301-128).

명세 31-4의 토픽·QoS·Retain 규칙과 31-10의 재연결·복구 정책을 구현한다.
ROS를 모르므로 브로커만 있으면 단독으로 시험할 수 있다.

설계에서 중요한 두 가지.

**연결 실패로 죽지 않는다.** `connect()`는 브로커가 없으면 예외를 던진다. 그래서
`connect_async()` + `loop_start()`를 쓴다. 브로커가 없어도 백그라운드 스레드가
계속 재시도하고, 그동안 발행 호출은 조용히 버려진다. 관제 링크가 끊겨도 카메라와
스트리밍은 계속 돌아야 한다(32장 장애 격리).

**LWT 본문은 접속 시점에 고정된다.** 나중에 바꿀 수 없으므로 `robot_id`만 담은
최소 형태로 만든다. 브로커는 젯슨이 죽은 뒤에 이걸 발행하므로, 그 시점의 상태를
넣으려 해도 알 수 없다.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .message_mapper import (
    MESSAGE_TYPE_PRESENCE,
    SCHEMA_VERSION,
    utc_now_iso,
)

# 31-4. 채널별 QoS와 Retain.
#
# Retain은 "지금 상태"인 presence와 state에만 쓴다. 새 구독자가 붙으면 브로커가
# 마지막 값을 즉시 준다. telemetry는 흘려보내는 값이라 Retain하면 낡은 값이
# 남는다.
#
# QoS 0은 telemetry에만 쓴다. 2Hz로 계속 오므로 하나 유실돼도 0.5초 뒤 다음 값이
# 온다. QoS 1로 올리면 재전송 큐가 쌓여 오히려 최신값이 늦어진다(31-10의
# "긴 backlog 금지").
CHANNEL_POLICY: dict[str, tuple[int, bool]] = {
    # 채널: (QoS, Retain)
    "presence": (1, True),
    "state": (1, True),
    "telemetry": (0, False),
    "events": (1, False),
    "acks": (1, False),
}

TOPIC_PREFIX = "sentinel/v1/robots"

# 31-10. 재연결 지수 백오프. paho가 min에서 시작해 max까지 두 배씩 늘린다.
RECONNECT_MIN_SECONDS = 1
RECONNECT_MAX_SECONDS = 30


def topic_for(robot_id: str, channel: str) -> str:
    """31-4의 토픽 형식: sentinel/v1/robots/{robotId}/{channel}"""
    return f"{TOPIC_PREFIX}/{robot_id}/{channel}"


def build_last_will(robot_id: str) -> dict[str, Any]:
    """브로커가 젯슨 대신 발행할 OFFLINE 메시지.

    `sequence`를 0으로 두는 이유는 이 메시지가 젯슨의 발행 순서에 속하지 않기
    때문이다. 브로커가 만드는 것이므로 젯슨의 카운터와 무관하다.

    `sentAt`은 접속 시점의 시각이 된다. 실제 단절 시각이 아니므로 백엔드는
    자신의 `receivedAt`을 단절 시각으로 써야 한다. 이 한계는 LWT의 구조적
    특성이다.
    """
    return {
        "schemaVersion": SCHEMA_VERSION,
        "messageId": "00000000-0000-4000-8000-000000000000",
        "messageType": MESSAGE_TYPE_PRESENCE,
        "robotId": robot_id,
        "missionId": None,
        "sequence": 0,
        "sentAt": utc_now_iso(),
        "data": {
            "robotId": robot_id,
            "status": "OFFLINE",
            "reason": "MQTT_CONNECTION_LOST",
        },
    }


class MqttPublisher:
    """브로커 연결과 채널별 발행을 관리한다."""

    def __init__(
        self,
        robot_id: str,
        host: str,
        port: int,
        *,
        username: str | None = None,
        password: str | None = None,
        tls_enabled: bool = True,
        tls_ca_certs: str | None = None,
        tls_insecure: bool = False,
        transport: str = 'websockets',
        ws_path: str = '/mqtt',
        keepalive: int = 30,
        protocol_version: int = 5,
        client_id: str | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.robot_id = robot_id
        self.host = host
        self.port = port
        self._logger = logger
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

        # 연결 여부를 자체적으로 들고 있는다. paho의 is_connected()는 재연결
        # 중간 상태에서 애매하고, 노드가 "지금 보낼 수 있나"를 물어봐야 한다.
        self._connected = threading.Event()

        # 명세 31-1이 MQTT 5를 지정하므로 기본값은 5다. Mosquitto는 5를
        # 지원한다.
        #
        # 3.1.1로 내릴 수 있게 둔 이유는 로컬 테스트 브로커 때문이다. 개발용으로
        # 쓰는 amqtt는 3.1.1까지만 지원해서, 5로 접속하면 CONNECT를 잘못 해석해
        # "Will flag set, but will topic/message not present in payload"로 세션을
        # 거부한다. LWT·Retain·QoS는 3.1.1에도 있으므로 로컬 검증에는 지장이 없고,
        # 31-14도 MQTT 5 고유 기능(Message Expiry)을 선택으로 분류했다.
        protocol = mqtt.MQTTv5 if protocol_version == 5 else mqtt.MQTTv311
        self._protocol_version = protocol_version
        # WebSocket으로 붙는다. 8883이 아니라 443이다(S15P11A301-103).
        #
        # EC2 실측에서 8883·1883은 패킷이 버려지고 443·8080·8989만 열려 있었다.
        # SSAFY 보안그룹이 표준 포트만 열어두고 터미널로는 새 포트를 열 수 없다.
        # MinIO 9000에서 이미 겪은 제약이다. 그래서 기존 443 vhost에 WebSocket
        # 경로를 얹었고 Nginx가 TLS를 종료한다.
        #
        # 토픽·QoS·Retain·LWT·봉투·ACL은 하나도 바뀌지 않았다. 접속 경로만 바뀐다.
        self._transport = transport
        self._ws_path = ws_path
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"{robot_id}-bridge",
            protocol=protocol,
            transport=transport,
        )
        if transport == 'websockets':
            self._client.ws_set_options(path=ws_path)

        if username:
            self._client.username_pw_set(username, password)

        # TLS 사용 여부와 CA 경로는 다른 것이다.
        #
        # 전에는 `if tls_ca_certs:`로 판단했다. 그러면 공인 인증서를 쓰는 운영에서
        # CA 경로가 비어 TLS가 조용히 꺼지고, `wss://`가 아니라 `ws://`로 443에
        # 붙어 실패한다. 로컬 자체 서명 브로커에서는 CA를 주므로 이 결함이
        # 드러나지 않았다.
        #
        # `tls_set()`을 CA 없이 부르면 시스템 CA 번들로 검증한다. Let's Encrypt
        # 인증서는 그것으로 충분하고, 젯슨이 인증서 파일을 들고 있지 않아도 된다.
        if tls_enabled:
            self._client.tls_set(ca_certs=tls_ca_certs or None)
            if tls_insecure:
                # 자체 서명 인증서로 시험할 때만 쓴다. 운영에서는 호스트명이
                # 인증서와 일치해야 한다.
                self._client.tls_insecure_set(True)
        self._tls_enabled = tls_enabled

        # LWT는 connect 전에 등록해야 한다. 접속 후에는 바꿀 수 없다.
        will_qos, will_retain = CHANNEL_POLICY["presence"]
        self._client.will_set(
            topic_for(robot_id, "presence"),
            json.dumps(build_last_will(robot_id), ensure_ascii=False),
            qos=will_qos,
            retain=will_retain,
        )

        self._client.reconnect_delay_set(
            min_delay=RECONNECT_MIN_SECONDS, max_delay=RECONNECT_MAX_SECONDS
        )
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect

        self._keepalive = keepalive

    # ------------------------------------------------------------------
    # 연결
    # ------------------------------------------------------------------

    def start(self) -> None:
        """비동기 접속을 시작한다. 브로커가 없어도 예외를 던지지 않는다."""
        self._client.connect_async(self.host, self.port, keepalive=self._keepalive)
        self._client.loop_start()
        # 실제 URL을 찍는다. TLS가 켜졌는지 로그에서 확인할 수 없으면, ws로
        # 443에 붙어 실패할 때 원인을 브로커 쪽에서 찾게 된다.
        self._log(
            "info",
            f"MQTT 접속 시도: {self.endpoint} (MQTT {self._protocol_version})",
        )

    @property
    def endpoint(self) -> str:
        """사람이 읽을 접속 주소. 로그와 진단에 쓴다."""
        if self._transport == 'websockets':
            scheme = 'wss' if self._tls_enabled else 'ws'
            return f'{scheme}://{self.host}:{self.port}{self._ws_path}'
        scheme = 'mqtts' if self._tls_enabled else 'mqtt'
        return f'{scheme}://{self.host}:{self.port}'

    def stop(self) -> None:
        """정상 종료. LWT가 발행되지 않도록 DISCONNECT를 보낸다.

        여기서 OFFLINE(SHUTDOWN)을 직접 보내는 것은 노드 책임이다. DISCONNECT를
        먼저 보내면 브로커가 LWT를 발행하지 않으므로, 정상 종료와 비정상 종료가
        관제에서 구분된다.
        """
        self._client.disconnect()
        self._client.loop_stop()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def _handle_connect(self, _client, _userdata, _flags, reason_code, _properties=None):
        if reason_code == 0:
            self._connected.set()
            self._log("info", f"MQTT 연결됨: {self.endpoint}")
            if self._on_connected:
                self._on_connected()
        else:
            self._connected.clear()
            # 인증 실패는 재시도해도 같은 결과다. 로그를 남겨 원인을 보이게 한다.
            self._log("error", f"MQTT 연결 거부: reason_code={reason_code}")

    def _handle_disconnect(
        self, _client, _userdata, _flags=None, reason_code=None, _properties=None
    ):
        was_connected = self._connected.is_set()
        self._connected.clear()
        if was_connected:
            self._log("warn", f"MQTT 연결 끊김: reason_code={reason_code}. 재연결한다")
            if self._on_disconnected:
                self._on_disconnected()

    # ------------------------------------------------------------------
    # 발행
    # ------------------------------------------------------------------

    def publish(self, channel: str, message: dict[str, Any]) -> bool:
        """채널 정책(31-4)에 따라 발행한다.

        연결이 없으면 False를 돌려주고 버린다. 큐에 쌓지 않는 이유는 31-10이
        일반 telemetry에 "긴 backlog 금지"를 명시했기 때문이다. 재전송이
        필요한 이벤트는 `outbox_repository`가 담당한다.
        """
        if channel not in CHANNEL_POLICY:
            raise ValueError(f"정의되지 않은 채널: {channel}")

        if not self._connected.is_set():
            return False

        qos, retain = CHANNEL_POLICY[channel]
        payload = json.dumps(message, ensure_ascii=False)
        info = self._client.publish(
            topic_for(self.robot_id, channel), payload, qos=qos, retain=retain
        )
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _log(self, level: str, message: str) -> None:
        """rclpy 로거에 남긴다. severity별로 호출 지점을 나눠야 한다.

        `getattr(logger, level)(message)` 한 줄로 감싸면 안 된다. rclpy 로거는
        호출 위치를 캐싱해 중복 제거를 지원하므로, 같은 위치에서 severity가 바뀌면
        이렇게 거부한다.

            ValueError: Logger severity cannot be changed between calls.

        이 예외가 재연결 콜백을 죽여 Outbox 재전송이 실행되지 않았다
        (S15P11A301-140). 브로커 연결이 info로 한 번 남고 끊김이 warn으로 남는
        순간 터진다. 조용히 실패하지 않고 콜백 전체를 무너뜨리므로 위험하다.
        """
        if self._logger is None:
            return
        if level == 'warn':
            self._logger.warn(message)
        elif level == 'error':
            self._logger.error(message)
        else:
            self._logger.info(message)

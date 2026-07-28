"""메시지 계약 시험 (S15P11A301-128).

브로커도 ROS도 없이 돈다. `message_mapper`와 `mqtt_client`가 `rclpy`를
import하지 않기 때문이며, 그래서 CI에서도 실행할 수 있다.

브로커를 띄우는 검증만으로는 부족하다. 로컬 테스트 브로커(amqtt)는 수신 QoS를
구독 QoS로 올려 보고하므로, 발행 QoS가 31-4를 지키는지 브로커로는 확인할 수
없다. 여기서 발행 호출을 직접 붙잡아 확인한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_bridge.message_mapper import MessageMapper  # noqa: E402
from sentinel_bridge.mqtt_client import (  # noqa: E402
    CHANNEL_POLICY,
    build_last_will,
    topic_for,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_DIR = REPO_ROOT / "common" / "schemas"

DATA_SCHEMA_BY_TYPE = {
    "ROBOT_PRESENCE": "presence.schema.json",
    "ROBOT_STATE": "state.schema.json",
    "ROBOT_TELEMETRY": "telemetry.schema.json",
}


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _validate(message: dict) -> None:
    """봉투와 본문을 common/schemas로 검증한다."""
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema가 없으면 계약 검증을 건너뛴다"
    )
    checker = jsonschema.Draft202012Validator.FORMAT_CHECKER

    envelope = jsonschema.Draft202012Validator(
        _load_schema("envelope.schema.json"), format_checker=checker
    )
    errors = list(envelope.iter_errors(message))
    assert not errors, f"봉투 위반: {[e.message for e in errors]}"

    schema_name = DATA_SCHEMA_BY_TYPE[message["messageType"]]
    body = jsonschema.Draft202012Validator(
        _load_schema(schema_name), format_checker=checker
    )
    errors = list(body.iter_errors(message["data"]))
    assert not errors, f"본문 위반: {[e.message for e in errors]}"


# ----------------------------------------------------------------------
# 31-4 토픽·QoS·Retain
# ----------------------------------------------------------------------


def test_topic_format_follows_spec():
    assert topic_for("SENTINEL-01", "telemetry") == (
        "sentinel/v1/robots/SENTINEL-01/telemetry"
    )


@pytest.mark.parametrize(
    ("channel", "qos", "retain"),
    [
        # 명세 31-4 표를 그대로 옮긴다. 이 시험이 깨지면 정책이 바뀐 것이므로
        # 명세를 먼저 확인해야 한다.
        ("presence", 1, True),
        ("state", 1, True),
        ("telemetry", 0, False),
        ("events", 1, False),
        ("acks", 1, False),
    ],
)
def test_channel_policy_matches_spec(channel, qos, retain):
    assert CHANNEL_POLICY[channel] == (qos, retain)


def test_retain_only_for_current_state_channels():
    """cmd/*가 Retain되면 과거 명령이 재연결 직후 실행된다(31-4).

    이 브리지는 cmd/*를 발행하지 않지만, 정책 표에 Retain이 켜진 채널이
    presence와 state뿐임을 못박아 둔다.
    """
    retained = {c for c, (_, retain) in CHANNEL_POLICY.items() if retain}
    assert retained == {"presence", "state"}


def test_publish_uses_channel_policy():
    """발행 호출이 채널별 QoS·Retain을 그대로 넘기는지 확인한다.

    브로커로는 확인할 수 없는 부분이다. amqtt가 수신 QoS를 구독 QoS로 올려
    보고하기 때문이다.
    """
    from sentinel_bridge import mqtt_client as module

    calls = []

    class FakeInfo:
        rc = 0  # mqtt.MQTT_ERR_SUCCESS

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def username_pw_set(self, *_a, **_k):
            pass

        def ws_set_options(self, *_a, **_k):
            pass

        def tls_set(self, *_a, **_k):
            pass

        def tls_insecure_set(self, *_a, **_k):
            pass

        def will_set(self, topic, payload, qos, retain):
            calls.append(("will", topic, payload, qos, retain))

        def reconnect_delay_set(self, **_k):
            pass

        def publish(self, topic, payload, qos, retain):
            calls.append(("publish", topic, payload, qos, retain))
            return FakeInfo()

    original = module.mqtt.Client
    module.mqtt.Client = FakeClient
    try:
        publisher = module.MqttPublisher("SENTINEL-01", "127.0.0.1", 1883)
        publisher._connected.set()  # 연결됐다고 가정한다
        for channel in ("presence", "state", "telemetry"):
            publisher.publish(channel, {"messageId": "x"})
    finally:
        module.mqtt.Client = original

    published = {
        topic.rsplit("/", 1)[-1]: (qos, retain)
        for kind, topic, _payload, qos, retain in calls
        if kind == "publish"
    }
    assert published["presence"] == (1, True)
    assert published["state"] == (1, True)
    assert published["telemetry"] == (0, False)


def test_last_will_is_registered_with_retain():
    """LWT는 presence 정책(QoS 1, Retain)으로 등록돼야 한다.

    Retain이 없으면 젯슨이 죽은 뒤 접속한 관제 화면이 OFFLINE을 못 본다.
    """
    from sentinel_bridge import mqtt_client as module

    calls = []

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def ws_set_options(self, *_a, **_k):
            pass

        def username_pw_set(self, *_a, **_k):
            pass

        def tls_set(self, *_a, **_k):
            pass

        def tls_insecure_set(self, *_a, **_k):
            pass

        def will_set(self, topic, payload, qos, retain):
            calls.append((topic, payload, qos, retain))

        def reconnect_delay_set(self, **_k):
            pass

    original = module.mqtt.Client
    module.mqtt.Client = FakeClient
    try:
        module.MqttPublisher("SENTINEL-01", "127.0.0.1", 1883)
    finally:
        module.mqtt.Client = original

    assert len(calls) == 1
    topic, payload, qos, retain = calls[0]
    assert topic == "sentinel/v1/robots/SENTINEL-01/presence"
    assert (qos, retain) == (1, True)
    body = json.loads(payload)["data"]
    assert body["status"] == "OFFLINE"
    assert body["reason"] == "MQTT_CONNECTION_LOST"


def test_last_will_passes_schema():
    _validate(build_last_will("SENTINEL-01"))


# ----------------------------------------------------------------------
# 31-5 봉투와 본문
# ----------------------------------------------------------------------


def test_presence_online_passes_schema():
    _validate(MessageMapper("SENTINEL-01").presence_online())


def test_presence_shutdown_distinguishes_from_lwt():
    """정상 종료와 비정상 종료가 reason으로 구분돼야 한다."""
    message = MessageMapper("SENTINEL-01").presence_offline_shutdown()
    _validate(message)
    assert message["data"]["reason"] == "SHUTDOWN"
    assert build_last_will("SENTINEL-01")["data"]["reason"] == "MQTT_CONNECTION_LOST"


def test_state_passes_schema():
    message = MessageMapper("SENTINEL-01").state(
        mission_state=None,
        control_mode=None,
        safety_state="SAFE_IDLE",
        components={"camera": True, "lidar": False},
    )
    _validate(message)


def test_telemetry_with_all_fields_null_passes_schema():
    """ESP32 연동 전 상태. 전체 형태를 유지하며 미확보 필드만 null이다."""
    message = MessageMapper("SENTINEL-01").telemetry()
    _validate(message)
    data = message["data"]
    for field in ("pose", "motion", "battery", "environment", "compute"):
        assert data[field] is None
    # health는 필수이므로 객체가 있어야 하고 내부만 null이다.
    assert data["health"] == {
        "mcuConnected": None,
        "lidarOk": None,
        "cameraOk": None,
    }


def test_telemetry_with_real_values_passes_schema():
    message = MessageMapper("SENTINEL-01").telemetry(
        compute={
            "cpuPercent": 31.5,
            "gpuPercent": 12.0,
            "memoryPercent": 54.1,
            "jetsonTempC": 52.5,
        },
        environment={"temperatureC": 28.4, "humidityPercent": 55.1},
        health={"mcuConnected": True, "lidarOk": True, "cameraOk": True},
    )
    _validate(message)


def test_sequence_increases_per_publisher():
    mapper = MessageMapper("SENTINEL-01")
    first = mapper.presence_online()["sequence"]
    second = mapper.presence_online()["sequence"]
    assert second == first + 1


def test_message_id_is_unique_per_message():
    """서버의 중복 차단이 messageId에 의존하므로 재사용하면 메시지가 삼켜진다."""
    mapper = MessageMapper("SENTINEL-01")
    ids = {mapper.telemetry()["messageId"] for _ in range(20)}
    assert len(ids) == 20


def test_sent_at_is_utc_with_z_suffix():
    """스키마가 Z를 강제한다. 지역 오프셋은 백엔드마다 다르게 해석될 수 있다."""
    stamp = MessageMapper("SENTINEL-01").presence_online()["sentAt"]
    assert stamp.endswith("Z")
    assert "+" not in stamp


def test_tls_stays_on_when_ca_path_is_empty():
    """공인 인증서를 쓰는 운영에서 TLS가 조용히 꺼지지 않아야 한다.

    전에는 `if tls_ca_certs:`로 TLS 사용 여부를 판단했다. 그러면 CA 경로가 빈
    운영 설정에서 TLS가 꺼지고, `wss://`가 아니라 `ws://`로 443에 붙어 실패한다.
    로컬 자체 서명 브로커에서는 CA를 주므로 이 결함이 드러나지 않았다.

    S15P11A301-103에서 접속 방식이 443 WebSocket으로 바뀌며 실제 위험이 됐다.
    """
    from sentinel_bridge import mqtt_client as module

    calls = []

    class FakeClient:
        def __init__(self, *_a, **kwargs):
            calls.append(("init", kwargs.get("transport")))

        def ws_set_options(self, **kwargs):
            calls.append(("ws", kwargs.get("path")))

        def username_pw_set(self, *_a, **_k):
            pass

        def tls_set(self, ca_certs=None):
            calls.append(("tls", ca_certs))

        def tls_insecure_set(self, *_a, **_k):
            calls.append(("insecure", None))

        def will_set(self, *_a, **_k):
            pass

        def reconnect_delay_set(self, **_k):
            pass

    original = module.mqtt.Client
    module.mqtt.Client = FakeClient
    try:
        publisher = module.MqttPublisher(
            "SENTINEL-01", "api.sentinel-ugv.xyz", 443, tls_ca_certs=""
        )
    finally:
        module.mqtt.Client = original

    assert ("tls", None) in calls, "CA 경로가 비어도 TLS를 켜야 한다"
    assert ("insecure", None) not in calls, "운영에서 검증을 끄면 안 된다"
    assert ("init", "websockets") in calls
    assert ("ws", "/mqtt") in calls
    assert publisher.endpoint == "wss://api.sentinel-ugv.xyz:443/mqtt"


def test_tls_can_be_disabled_for_local_broker():
    """로컬 평문 WebSocket 브로커로 검증할 때만 끈다."""
    from sentinel_bridge import mqtt_client as module

    calls = []

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def ws_set_options(self, *_a, **_k):
            pass

        def username_pw_set(self, *_a, **_k):
            pass

        def tls_set(self, *_a, **_k):
            calls.append("tls")

        def will_set(self, *_a, **_k):
            pass

        def reconnect_delay_set(self, **_k):
            pass

    original = module.mqtt.Client
    module.mqtt.Client = FakeClient
    try:
        publisher = module.MqttPublisher(
            "SENTINEL-01", "127.0.0.1", 19883, tls_enabled=False
        )
    finally:
        module.mqtt.Client = original

    assert "tls" not in calls
    assert publisher.endpoint == "ws://127.0.0.1:19883/mqtt"

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
    "INTERACTION_REPORT": "interaction-report.schema.json",
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
        publisher = module.MqttClient("SENTINEL-01", "127.0.0.1", 1883)
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
        module.MqttClient("SENTINEL-01", "127.0.0.1", 1883)
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


def test_telemetry_with_esp32_converted_values_passes_schema():
    """변환 함수의 출력이 그대로 스키마를 통과하는지 본다 (S15P11A301-213).

    위 시험은 본문을 손으로 적어 넣으므로, 실제로 전송되는 값(변환 함수의 출력)이
    스키마에 맞는지는 확인하지 않는다. 습도 단위를 틀리거나 NaN이 새는 것은
    변환 쪽에서 생기는 일이라 이 경로로 붙잡아야 한다.
    """
    from sentinel_bridge.message_mapper import (  # noqa: E402
        environment_payload,
        motion_payload,
    )

    message = MessageMapper("SENTINEL-01").telemetry(
        # DHT11이 보내는 비율과 엔코더가 보내는 twist 값을 그대로 넣는다.
        environment=environment_payload(28.2, 0.651),
        motion=motion_payload(0.32, -0.15),
        health={"mcuConnected": True, "lidarOk": True, "cameraOk": True},
    )
    _validate(message)

    data = message["data"]
    assert data["environment"]["humidityPercent"] == 65.1
    assert data["motion"]["linearVelocityMps"] == 0.32
    # NaN이 섞이면 표준 JSON으로 직렬화되지 않는다. MQTT로 나가는 형태로 확인한다.
    assert json.dumps(message, allow_nan=False)


def test_telemetry_drops_esp32_values_when_sensor_reads_fail():
    """DHT11 읽기 실패(NaN)가 봉투 전체를 깨뜨리지 않아야 한다.

    변환이 None을 돌려주면 필드가 null로 남고, 스키마가 null을 허용하므로
    봉투는 유효하다. 값 하나 때문에 telemetry가 통째로 버려지면 임무 궤적에
    구멍이 생긴다.
    """
    from sentinel_bridge.message_mapper import environment_payload  # noqa: E402

    message = MessageMapper("SENTINEL-01").telemetry(
        environment=environment_payload(float("nan"), 0.651),
    )
    _validate(message)
    assert message["data"]["environment"] is None
    assert json.dumps(message, allow_nan=False)


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


def test_interaction_report_passes_schema_and_keeps_mission_id():
    data = {
        "interactionId": "74ebbf7d-5726-4c4a-95b2-b899afe8543a",
        "encounterId": "32f6f147-dacc-4979-a9a2-7aab8fed689c",
        "missionId": "1cb5350f-187f-4478-b95e-bb513c47e706",
        "visionPersonCount": 3,
        "encounterPose": {
            "x": 12.4,
            "y": 7.8,
            "yaw": 1.57,
            "mapId": "floor-1",
        },
        "additionalPersonReports": [
            {
                "subjectText": "우리 아기",
                "reportedCount": 1,
                "countStatus": "EXACT",
                "locationText": "2층",
                "reportedFloor": 2,
                "groundingStatus": "UNGROUNDED",
                "responseStatus": "UNKNOWN",
                "certaintyStatus": "ASSERTED",
                "rawUtterance": "2층에 우리 아기가 있어요",
                "verificationStatus": "UNVERIFIED",
                "operatorReviewRequired": True,
            }
        ],
        "startedAt": "2026-07-30T09:16:12.003Z",
        "endedAt": "2026-07-30T09:17:30.994Z",
        "sessionReport": {
            "responseScope": "GROUP",
            "anyResponseDetected": True,
            "reportedResponsiveCount": 2,
            "reportedCountStatus": "SELF_REPORTED_GROUP_COUNT",
            "countConfidence": None,
            "mobilityStatus": "NO",
            "urgentConditionReported": "YES",
            "operatorReviewRequired": True,
            "terminationReason": "NORMAL",
        },
        "riskAssessment": {
            "riskLevel": "IMMEDIATE",
            "riskReasons": ["긴급 상태가 있다고 발화함"],
            "ruleVersion": "voice-risk-v1.0",
            "operatorReviewRequired": True,
        },
        "usedFallback": False,
    }
    message = MessageMapper("SENTINEL-01").interaction_report(data)

    _validate(message)
    assert message["missionId"] == data["missionId"]
    assert message["messageId"] == data["interactionId"]


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
        publisher = module.MqttClient(
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
        publisher = module.MqttClient(
            "SENTINEL-01", "127.0.0.1", 19883, tls_enabled=False
        )
    finally:
        module.mqtt.Client = original

    assert "tls" not in calls
    assert publisher.endpoint == "ws://127.0.0.1:19883/mqtt"


def test_every_mission_state_maps_to_a_safety_state():
    """26.2 상태가 늘면 safetyState 매핑도 함께 늘어야 한다.

    빠뜨리면 정지해야 하는 상태가 관제에 주행 중으로 보인다. 노드는 매핑 실패 시
    STOPPED로 보내고 경고를 남기지만, 그 전에 여기서 잡는 편이 낫다.
    """
    import importlib
    import sys
    from pathlib import Path

    # test/ → sentinel_bridge → src 이므로 parents[2]가 src다.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'sentinel_mission'))
    try:
        mission_state = importlib.import_module('sentinel_mission.mission_state')
    except ImportError:
        import pytest as _pytest

        _pytest.skip('sentinel_mission이 없다. 같은 워크스페이스에서만 검사한다')

    # cloud_bridge_node가 아니라 message_mapper에서 가져온다. 노드는 rclpy를
    # import하므로 ROS 없는 CI 컨테이너에서 실패한다.
    from sentinel_bridge.message_mapper import SAFETY_STATE_BY_MISSION_STATE

    missing = [
        state.value
        for state in mission_state.MissionState
        if state.value not in SAFETY_STATE_BY_MISSION_STATE
    ]
    assert not missing, f'safetyState 매핑이 없는 상태: {missing}'

    allowed = {'SAFE_IDLE', 'READY', 'RUNNING', 'STOPPED', 'ESTOP', 'FAULT'}
    wrong = {
        key: value
        for key, value in SAFETY_STATE_BY_MISSION_STATE.items()
        if value not in allowed
    }
    assert not wrong, f'state.schema.json의 enum에 없는 값: {wrong}'


def test_states_that_forbid_movement_are_not_reported_as_running():
    """이동을 허용하지 않는 상태가 관제에 RUNNING으로 가면 안 된다."""
    # cloud_bridge_node가 아니라 message_mapper에서 가져온다. 노드는 rclpy를
    # import하므로 ROS 없는 CI 컨테이너에서 실패한다.
    from sentinel_bridge.message_mapper import SAFETY_STATE_BY_MISSION_STATE

    for state in ('INTERACTING', 'POST_RECORDING', 'REPORTING', 'PAUSED'):
        assert SAFETY_STATE_BY_MISSION_STATE[state] != 'RUNNING', state
    assert SAFETY_STATE_BY_MISSION_STATE['ESTOP'] == 'ESTOP'
    assert SAFETY_STATE_BY_MISSION_STATE['ERROR'] == 'FAULT'


def test_yaw_from_quaternion_matches_known_rotations():
    """2D SLAM의 yaw 계산. tf_transformations가 없어 직접 구현했다.

    부호를 틀리면 관제 지도에서 로봇이 반대 방향을 본다. REP-103은 반시계
    방향을 양수로 정한다.
    """
    import math

    from sentinel_bridge.message_mapper import yaw_from_quaternion

    cases = [
        # (x, y, z, w, 기대 각도(도))
        (0.0, 0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4), 90.0),
        (0.0, 0.0, math.sin(-math.pi / 4), math.cos(-math.pi / 4), -90.0),
        (0.0, 0.0, math.sin(math.pi / 6), math.cos(math.pi / 6), 60.0),
    ]
    for x, y, z, w, expected in cases:
        actual = math.degrees(yaw_from_quaternion(x, y, z, w))
        assert abs(actual - expected) < 1e-6, f'{(x, y, z, w)} → {actual}, 기대 {expected}'

    # 180도는 +180과 -180이 같은 회전이므로 절댓값으로 본다.
    assert abs(math.degrees(yaw_from_quaternion(0.0, 0.0, 1.0, 0.0))) == 180.0


def test_pose_body_satisfies_the_telemetry_schema():
    """SLAM이 채우는 pose가 계약을 만족하는지 (S15P11A301-137)."""
    import math

    jsonschema = pytest.importorskip('jsonschema')
    from sentinel_bridge.message_mapper import MessageMapper, yaw_from_quaternion

    schema = _load_schema('telemetry.schema.json')
    validator = jsonschema.Draft202012Validator(schema)

    mapper = MessageMapper('SENTINEL-01')
    pose = {
        'x': 1.234,
        'y': -0.567,
        'yaw': round(yaw_from_quaternion(0.0, 0.0, math.sin(0.3), math.cos(0.3)), 4),
        'mapId': 'c81f6d20-5a47-4e93-b2d8-1f70e4a95c33',
    }
    message = mapper.telemetry(pose=pose)
    errors = list(validator.iter_errors(message['data']))
    assert not errors, [error.message for error in errors]

    # SLAM이 없을 때는 null이다. 값을 지어내면 관제가 원점에 로봇을 그린다.
    absent = mapper.telemetry(pose=None)
    assert absent['data']['pose'] is None
    assert not list(validator.iter_errors(absent['data']))


def test_encounter_envelope_satisfies_the_contract():
    """encounter를 events 채널로 보낼 때 봉투와 본문이 계약을 만족하는지.

    본문은 mission_manager가 만든 것을 그대로 넘긴다. 여기서 다시 조립하면 두
    곳이 어긋난다(S15P11A301-140).
    """
    jsonschema = pytest.importorskip('jsonschema')
    from sentinel_bridge.message_mapper import MessageMapper

    envelope_schema = _load_schema('envelope.schema.json')
    data_schema = _load_schema('encounter.schema.json')
    checker = jsonschema.Draft202012Validator.FORMAT_CHECKER
    envelope_validator = jsonschema.Draft202012Validator(
        envelope_schema, format_checker=checker
    )
    data_validator = jsonschema.Draft202012Validator(data_schema, format_checker=checker)

    body = {
        'encounterId': 'b9c43b74-e7f9-4f74-8358-9656293bc1af',
        'phase': 'CONFIRMED',
        'detectedAt': '2026-07-29T05:12:30.100Z',
        'personCount': 2,
        'trackIds': [7, 8],
        'confidence': 0.91,
        'pose': None,
        'missionId': '4a43f45c-779f-4df5-ac04-1695724829a4',
    }
    message = MessageMapper('SENTINEL-01').encounter(body)

    assert message['messageType'] == 'ENCOUNTER_CONFIRMED'
    # 봉투의 missionId를 본문과 맞춘다. 백엔드가 봉투를 우선하고 없으면 본문을 본다.
    assert message['missionId'] == body['missionId']
    assert not list(envelope_validator.iter_errors(message))
    assert not list(data_validator.iter_errors(message['data']))


def test_encounter_envelope_carries_every_phase():
    """phase가 달라도 messageType은 ENCOUNTER_CONFIRMED다.

    봉투 enum이 그것 하나이고 백엔드는 본문의 phase로 INSERT와 UPDATE를 가른다
    (S15P11A301-138). CONFIRMED만 보내면 관제가 상호작용 진행과 종료를 못 본다.
    """
    jsonschema = pytest.importorskip('jsonschema')
    from sentinel_bridge.message_mapper import MessageMapper

    data_validator = jsonschema.Draft202012Validator(
        _load_schema('encounter.schema.json'),
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )
    mapper = MessageMapper('SENTINEL-01')
    for phase in ('CONFIRMED', 'APPROACHED', 'ENDED', 'REDETECTED', 'LOST'):
        body = {
            'encounterId': 'b9c43b74-e7f9-4f74-8358-9656293bc1af',
            'phase': phase,
            'detectedAt': '2026-07-29T05:12:30.100Z',
            'personCount': 1,
            'missionId': '4a43f45c-779f-4df5-ac04-1695724829a4',
        }
        message = mapper.encounter(body)
        assert message['messageType'] == 'ENCOUNTER_CONFIRMED', phase
        assert not list(data_validator.iter_errors(message['data'])), phase


def test_events_channel_policy_is_qos1_without_retain():
    """31-4: events는 QoS 1, Retain 없음.

    Retain을 켜면 새 구독자가 옛 이벤트를 현재 상태로 오해한다. QoS 0으로 내리면
    사람을 발견한 사실이 유실될 수 있다.
    """
    from sentinel_bridge.mqtt_client import CHANNEL_POLICY

    assert CHANNEL_POLICY['events'] == (1, False)


def test_mission_signal_schema_has_mission_id():
    """MISSION_START가 missionId를 담을 자리가 있어야 한다.

    없으면 젯슨이 임무를 모르고, 발행한 encounter가 백엔드에서 버려진다
    (encounters.mission_id NOT NULL FK).
    """
    schema = _load_schema('mission-signal.schema.json')
    field = schema['properties'].get('missionId')
    assert field is not None, 'missionId 필드가 없다'
    assert 'null' in field['type']
    assert field['pattern'].startswith('^[0-9a-f]{8}-')


def test_log_helper_survives_severity_changes():
    """같은 `_log` 헬퍼로 info와 warn을 번갈아 써도 터지면 안 된다.

    rclpy 로거는 호출 위치를 캐싱해 중복 제거를 지원한다. `getattr(logger, level)`
    한 줄로 감싸면 모든 severity가 같은 위치에서 나가고, 두 번째 severity에서
    이렇게 거부한다.

        ValueError: Logger severity cannot be changed between calls.

    이 예외가 재연결 콜백을 죽여 Outbox 재전송이 실행되지 않았다
    (S15P11A301-140). 브로커가 붙었다 끊기는 순간 터지므로 단절 복구 경로 전체가
    무너진다.
    """
    from sentinel_bridge import mqtt_client as module

    calls = []

    class StrictLogger:
        """rclpy와 같은 제약을 흉내낸다. 호출 위치별로 severity를 고정한다."""

        def __init__(self):
            self._severity_by_site = {}

        def _record(self, severity, message):
            import inspect

            frame = inspect.stack()[2]
            site = (frame.filename, frame.lineno)
            previous = self._severity_by_site.setdefault(site, severity)
            if previous != severity:
                raise ValueError(
                    'Logger severity cannot be changed between calls.'
                )
            calls.append((severity, message))

        def info(self, message):
            self._record('info', message)

        def warn(self, message):
            self._record('warn', message)

        def error(self, message):
            self._record('error', message)

    class FakeClient:
        def __init__(self, *_a, **_k):
            pass

        def ws_set_options(self, *_a, **_k):
            pass

        def username_pw_set(self, *_a, **_k):
            pass

        def tls_set(self, *_a, **_k):
            pass

        def will_set(self, *_a, **_k):
            pass

        def reconnect_delay_set(self, **_k):
            pass

    original = module.mqtt.Client
    module.mqtt.Client = FakeClient
    try:
        publisher = module.MqttClient(
            'SENTINEL-01', '127.0.0.1', 443, logger=StrictLogger()
        )
    finally:
        module.mqtt.Client = original

    # 같은 헬퍼로 세 severity를 번갈아 쓴다. 터지면 재연결 경로가 무너진다.
    publisher._log('info', '연결됨')
    publisher._log('warn', '끊김')
    publisher._log('error', '실패')
    publisher._log('info', '다시 연결됨')

    assert [level for level, _ in calls] == ['info', 'warn', 'error', 'info']


# ----------------------------------------------------------------------
# 관제 명령 구독과 ACK (S15P11A301-143)
# ----------------------------------------------------------------------


def _command_type_enum() -> list[str]:
    schema = _load_schema("mission-command.schema.json")
    return schema["properties"]["type"]["enum"]


def _signal_enum() -> list[str]:
    schema = _load_schema("mission-signal.schema.json")
    return schema["properties"]["signal"]["enum"]


def test_every_command_type_is_either_mapped_or_deliberately_rejected():
    """계약의 명령을 빠뜨리면 관제 버튼이 조용히 죽는다.

    `COMMAND_TO_SIGNAL`에 없는 명령은 bridge가 `NOT_IMPLEMENTED`로 거부한다. 즉
    빠뜨려도 예외는 안 나고 관제만 거부를 받는다. 그래서 "빠뜨린 것"과 "일부러
    안 넣은 것"을 여기서 못 가리면 아무도 모른다.

    지금 일부러 안 넣은 것은 `RETURN` 하나다. `RETURNING`이 `UNIMPLEMENTED`이므로
    신호를 만들면 갈 수 없는 상태로 보내게 된다.
    """
    from sentinel_bridge.message_mapper import COMMAND_TO_SIGNAL

    deliberately_unmapped = {"RETURN"}
    for command_type in _command_type_enum():
        mapped = command_type in COMMAND_TO_SIGNAL
        expected = command_type not in deliberately_unmapped
        assert mapped is expected, (
            f'{command_type}: 매핑 {mapped}, 기대 {expected}. '
            '계약에 명령이 추가됐으면 매핑하거나 이 시험의 예외 목록에 넣는다'
        )


def test_mapped_signals_exist_in_the_signal_contract():
    """매핑한 신호가 `/mission/signal` enum에 없으면 mission_manager가 버린다.

    그 경우 로그에 "모르는 signal"만 남고 ACK는 오지 않아 관제가 PENDING에
    머문다. 오타 하나로 그렇게 된다.
    """
    from sentinel_bridge.message_mapper import COMMAND_TO_SIGNAL

    allowed = set(_signal_enum())
    for command_type, signal in COMMAND_TO_SIGNAL.items():
        assert signal in allowed, f'{command_type} → {signal}이 계약에 없다'


def test_stop_maps_to_mission_completed_and_resume_to_approved():
    """두 매핑은 근거가 있어서 고정한다.

    `RESUME`이 `RESUME_REQUESTED`가 아니다. 그것은 음성 쪽이 보고를 마치고 탐사를
    이어가겠다는 요청이며 REPORTING에서만 유효하다. PAUSED를 푸는 것은 30.5가
    자동 재개를 금지했으므로 운영자의 명시적 `RESUME_APPROVED`뿐이다.

    `STOP`은 26.3의 COMPLETED로 간다. 23.4가 "사용자 종료"를 종료 조건으로 뒀다.
    """
    from sentinel_bridge.message_mapper import COMMAND_TO_SIGNAL

    assert COMMAND_TO_SIGNAL["RESUME"] == "RESUME_APPROVED"
    assert COMMAND_TO_SIGNAL["STOP"] == "MISSION_COMPLETED"
    assert COMMAND_TO_SIGNAL["START"] == "MISSION_START"
    assert COMMAND_TO_SIGNAL["PAUSE"] == "PAUSE_REQUESTED"


def test_mode_commands_map_to_requests_not_facts():
    """`*_ENGAGED`(사실)가 아니라 `*_REQUESTED`(의도)여야 한다 (S15P11A301-298).

    모터 ESP32 가 「자율」을 거부할 수 있다 - 최근 500ms 안에 모바일 조종 입력이
    있었으면 거부한다. 관제 버튼을 바로 사실로 바꾸면 젯슨은 자율이라고 믿는데
    보드는 여전히 사람에게 바퀴를 주고 있는 상태가 된다.

    그 왕복은 `mission_manager` 의 `mode_gateway` 가 맡고 이 표는 번역만 한다.
    """
    from sentinel_bridge.message_mapper import COMMAND_TO_SIGNAL

    assert COMMAND_TO_SIGNAL["MANUAL"] == "MANUAL_REQUESTED"
    assert COMMAND_TO_SIGNAL["AUTO"] == "AUTO_REQUESTED"
    assert "MANUAL_ENGAGED" not in COMMAND_TO_SIGNAL.values()
    assert "AUTO_ENGAGED" not in COMMAND_TO_SIGNAL.values()


def test_command_ack_envelope_and_body_satisfy_the_contract():
    """ACK 봉투와 본문이 계약을 만족하는지.

    형식이 틀리면 백엔드가 조용히 버리고 `control_commands.result`가 PENDING으로
    남는다. 관제에서는 "명령이 안 먹혔다"로만 보인다.
    """
    jsonschema = pytest.importorskip("jsonschema")
    checker = jsonschema.Draft202012Validator.FORMAT_CHECKER

    mapper = MessageMapper("SENTINEL-01")
    body = {
        "commandId": "3f2a91c4-5d6e-4a7b-8c9d-0e1f2a3b4c5d",
        "status": "EXECUTED",
        "reasonCode": None,
        "message": None,
    }
    envelope = mapper.command_ack(body, mission_id="4bde8ad1-c74b-4d42-bec3-9f71af94b41a")

    assert envelope["messageType"] == "COMMAND_ACK"
    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema("envelope.schema.json"), format_checker=checker
        ).iter_errors(envelope)
    )
    assert not errors, [error.message for error in errors]

    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema("command-ack.schema.json"), format_checker=checker
        ).iter_errors(envelope["data"])
    )
    assert not errors, [error.message for error in errors]


def test_rejection_ack_carries_a_reason_code():
    """거부 ACK에 `reasonCode`가 있어야 관제가 화면을 분기할 수 있다."""
    jsonschema = pytest.importorskip("jsonschema")

    mapper = MessageMapper("SENTINEL-01")
    body = {
        "commandId": "3f2a91c4-5d6e-4a7b-8c9d-0e1f2a3b4c5d",
        "status": "REJECTED",
        "reasonCode": "ESTOP_ACTIVE",
        "message": "ESTOP는 latch 상태다. 운영자 조치가 필요하다",
    }
    envelope = mapper.command_ack(body)
    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema("command-ack.schema.json")
        ).iter_errors(envelope["data"])
    )
    assert not errors, [error.message for error in errors]


def test_acks_channel_policy_is_qos1_without_retain():
    """31-4. ACK는 잃으면 안 되고(QoS 1) 과거 응답이 남아서도 안 된다(Retain X)."""
    assert CHANNEL_POLICY["acks"] == (1, False)


def test_cmd_mission_is_subscribed_at_qos1():
    """명령은 잃으면 안 된다. QoS 0으로 구독하면 조용히 사라진다."""
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION, SUBSCRIBE_QOS

    assert SUBSCRIBE_QOS[CHANNEL_CMD_MISSION] == 1


def _fake_client_class(record: dict):
    """paho Client를 대신한다. 구독·발행 호출을 기록한다."""

    class FakeClient:
        def __init__(self, *_a, **_k):
            record['subscribed'] = []
            record['on_connect'] = None
            record['on_message'] = None

        def ws_set_options(self, *_a, **_k):
            pass

        def username_pw_set(self, *_a, **_k):
            pass

        def tls_set(self, *_a, **_k):
            pass

        def tls_insecure_set(self, *_a, **_k):
            pass

        def will_set(self, *_a, **_k):
            pass

        def reconnect_delay_set(self, **_k):
            pass

        def subscribe(self, topic, qos=0):
            record['subscribed'].append((topic, qos))
            return (0, 1)

        def __setattr__(self, name, value):
            if name in ('on_connect', 'on_message', 'on_disconnect'):
                record[name] = value
            object.__setattr__(self, name, value)

    return FakeClient


def _client_with_fake(monkeypatch, record):
    from sentinel_bridge import mqtt_client as module

    monkeypatch.setattr(module.mqtt, 'Client', _fake_client_class(record))
    return module.MqttClient('SENTINEL-01', 'broker', 443)


def test_subscribe_before_connect_is_applied_on_connect(monkeypatch):
    """접속 전에 등록한 구독이 접속 시 걸려야 한다.

    노드 초기화 순서가 구독에 영향을 주면 안 된다. 걸리지 않으면 관제 명령이
    영원히 도착하지 않고, 원인을 브로커 쪽에서 찾게 된다.
    """
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)
    client.subscribe(CHANNEL_CMD_MISSION, lambda _payload: None)

    assert record['subscribed'] == [], '접속 전에는 구독을 걸지 않는다'

    record['on_connect'](None, None, None, 0, None)
    assert record['subscribed'] == [
        ('sentinel/v1/robots/SENTINEL-01/cmd/mission', 1)
    ]


def test_reconnect_resubscribes(monkeypatch):
    """재연결 때 다시 구독해야 한다.

    paho는 clean session에서 재접속하면 구독을 잃는다. 다시 걸지 않으면 브로커
    단절 이후 관제 명령이 영원히 도착하지 않는다. 발행은 되므로 관제에는 로봇이
    정상으로 보인다 — 그래서 알아채기 어렵다.
    """
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)
    client.subscribe(CHANNEL_CMD_MISSION, lambda _payload: None)

    record['on_connect'](None, None, None, 0, None)
    record['on_disconnect'](None, None, None, 7, None)
    record['on_connect'](None, None, None, 0, None)

    assert len(record['subscribed']) == 2, '재연결 후 다시 구독해야 한다'


def test_failed_connection_does_not_subscribe(monkeypatch):
    """연결이 거부되면 구독하지 않는다. 인증 실패에 구독을 걸면 의미가 없다."""
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)
    client.subscribe(CHANNEL_CMD_MISSION, lambda _payload: None)
    record['on_connect'](None, None, None, 5, None)
    assert record['subscribed'] == []


def test_message_handler_receives_parsed_envelope(monkeypatch):
    """핸들러는 파싱된 봉투를 받는다.

    문자열을 넘기면 호출자마다 JSON 해석과 오류 처리를 반복하고, 그중 하나가
    예외를 흘리면 paho의 네트워크 스레드가 죽어 재연결까지 멈춘다.
    """
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)
    seen: list = []
    client.subscribe(CHANNEL_CMD_MISSION, seen.append)

    class Message:
        topic = 'sentinel/v1/robots/SENTINEL-01/cmd/mission'
        payload = json.dumps({'messageType': 'MISSION_COMMAND'}).encode()

    record['on_message'](None, None, Message())
    assert seen == [{'messageType': 'MISSION_COMMAND'}]


def test_handler_exception_does_not_escape(monkeypatch):
    """핸들러가 터져도 예외가 밖으로 나가면 안 된다.

    이 콜백은 paho의 네트워크 스레드에서 돈다. 예외가 나가면 그 스레드가 죽고
    재연결도 멈춘다. S15P11A301-140에서 `_log`의 예외가 재연결 콜백을 죽여 Outbox
    재전송이 실행되지 않은 것과 같은 구조의 사고다.
    """
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)

    def explode(_payload):
        raise RuntimeError('핸들러 결함')

    client.subscribe(CHANNEL_CMD_MISSION, explode)

    class Message:
        topic = 'sentinel/v1/robots/SENTINEL-01/cmd/mission'
        payload = b'{"messageType": "MISSION_COMMAND"}'

    record['on_message'](None, None, Message())  # 예외가 나가면 이 시험이 실패한다


def test_malformed_payload_does_not_reach_the_handler(monkeypatch):
    """JSON이 아니거나 객체가 아니면 핸들러를 부르지 않는다."""
    from sentinel_bridge.mqtt_client import CHANNEL_CMD_MISSION

    record: dict = {}
    client = _client_with_fake(monkeypatch, record)
    seen: list = []
    client.subscribe(CHANNEL_CMD_MISSION, seen.append)

    for raw in (b'not json', b'[1, 2, 3]', b'"string"', b'\xff\xfe'):
        class Message:
            topic = 'sentinel/v1/robots/SENTINEL-01/cmd/mission'
            payload = raw

        record['on_message'](None, None, Message())

    assert seen == []


def test_channel_lookup_handles_slashes_in_channel_names(monkeypatch):
    """`cmd/mission`처럼 슬래시가 든 채널을 되짚을 수 있어야 한다.

    토픽의 마지막 조각만 보면 `mission`이 되어 채널을 못 찾고, 명령이 조용히
    버려진다.
    """
    record: dict = {}
    client = _client_with_fake(monkeypatch, record)

    assert client._channel_of(
        'sentinel/v1/robots/SENTINEL-01/cmd/mission'
    ) == 'cmd/mission'
    assert client._channel_of('sentinel/v1/robots/OTHER/cmd/mission') is None
    assert client._channel_of('sentinel/v1/robots/SENTINEL-01/state') is None


# ----------------------------------------------------------------------
# 명령 중복 처리 (S15P11A301-143)
#
# 이 경로는 실물로 재현할 수 없다. 브로커 재전송에서만 생기고, 브로커 ACL이
# 로봇 계정의 cmd/* 발행을 막으므로(옳은 설정) 젯슨에서 중복을 만들 수 없다.
# 그래서 시험으로 고정한다.
# ----------------------------------------------------------------------


def _command_envelope(command_id: str, command_type: str = 'PAUSE') -> dict:
    return {
        'schemaVersion': '1.0',
        'messageId': '11111111-2222-4333-8444-555555555555',
        'messageType': 'MISSION_COMMAND',
        'robotId': 'SENTINEL-01',
        'missionId': '4bde8ad1-c74b-4d42-bec3-9f71af94b41a',
        'sequence': 1,
        'sentAt': '2026-07-29T07:00:00.000Z',
        'data': {'commandId': command_id, 'type': command_type},
    }


def _relay():
    from sentinel_bridge.command_relay import CommandRelay

    return CommandRelay()


def test_first_command_is_forwarded_as_a_signal():
    relay = _relay()
    decision = relay.decide(
        _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),
        mission_manager_alive=True,
    )
    assert decision.signal == 'PAUSE_REQUESTED'
    assert decision.ack is None
    assert relay.inflight_count == 1


def test_duplicate_while_inflight_is_dropped_not_re_forwarded():
    """결과를 기다리는 중에 온 중복은 버린다.

    다시 넣으면 mission_manager가 DUPLICATE_COMMAND로 거부하고, 그 거부가 ACK로
    나가 백엔드의 기록을 덮어쓴다.
    """
    relay = _relay()
    envelope = _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
    relay.decide(envelope, mission_manager_alive=True)

    second = relay.decide(envelope, mission_manager_alive=True)
    assert second.signal is None, '신호를 두 번 넣으면 안 된다'
    assert second.ack is None, '결과가 아직 없으므로 회신할 ACK도 없다'


def test_duplicate_after_result_replays_the_same_ack():
    """회신이 끝난 명령의 중복에는 **같은 ACK**를 다시 보낸다.

    이것이 이 모듈의 핵심이다. 재전송에 같은 응답을 주는 것이 멱등의 정의이고,
    다른 응답을 주면 백엔드가 EXECUTED를 REJECTED로 덮어쓴다.
    """
    relay = _relay()
    command_id = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
    envelope = _command_envelope(command_id)
    relay.decide(envelope, mission_manager_alive=True)

    executed = {
        'commandId': command_id,
        'status': 'EXECUTED',
        'reasonCode': None,
        'message': None,
    }
    assert relay.resolve(executed) == command_id

    third = relay.decide(envelope, mission_manager_alive=True)
    assert third.signal is None
    assert third.ack == executed, '같은 ACK여야 한다'
    assert third.replayed is True


def test_return_is_rejected_with_not_implemented():
    """RETURN은 계약에 있으나 RETURNING이 미구현이다.

    조용히 무시하면 관제가 영원히 PENDING을 본다.
    """
    from sentinel_bridge.command_relay import REASON_NOT_IMPLEMENTED

    relay = _relay()
    decision = relay.decide(
        _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', 'RETURN'),
        mission_manager_alive=True,
    )
    assert decision.signal is None
    assert decision.ack['status'] == 'REJECTED'
    assert decision.ack['reasonCode'] == REASON_NOT_IMPLEMENTED


def test_mission_manager_down_is_rejected_rather_than_left_pending():
    """받을 노드가 없으면 신호를 넣지 않고 거부한다.

    넣고 기다리면 ACK가 오지 않아 관제가 PENDING에 머문다.
    """
    from sentinel_bridge.command_relay import REASON_MISSION_MANAGER_DOWN

    relay = _relay()
    decision = relay.decide(
        _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),
        mission_manager_alive=False,
    )
    assert decision.signal is None
    assert decision.ack['reasonCode'] == REASON_MISSION_MANAGER_DOWN


def test_command_without_command_id_is_dropped_silently_but_noted():
    """`commandId`가 없으면 회신 대상을 특정할 수 없어 버린다. 사유는 남긴다."""
    relay = _relay()
    envelope = _command_envelope('x')
    envelope['data'].pop('commandId')
    decision = relay.decide(envelope, mission_manager_alive=True)
    assert decision.signal is None
    assert decision.ack is None
    assert decision.note


def test_wrong_message_type_is_not_treated_as_a_command():
    """다른 messageType이 `cmd/mission`에 오면 명령으로 다루지 않는다."""
    relay = _relay()
    envelope = _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
    envelope['messageType'] = 'MANUAL_DRIVE_COMMAND'
    decision = relay.decide(envelope, mission_manager_alive=True)
    assert decision.signal is None
    assert decision.ack is None


def test_malformed_type_is_rejected_with_a_reason_code():
    from sentinel_bridge.command_relay import REASON_MALFORMED_COMMAND

    relay = _relay()
    envelope = _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
    envelope['data']['type'] = 42
    decision = relay.decide(envelope, mission_manager_alive=True)
    assert decision.ack['reasonCode'] == REASON_MALFORMED_COMMAND


def test_ack_cache_is_bounded():
    """캐시가 무한히 자라면 긴 임무에서 메모리가 늘어난다."""
    from sentinel_bridge.command_relay import CommandRelay

    relay = CommandRelay(ack_cache_size=4)
    for index in range(10):
        command_id = f'{index:08d}-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        relay.resolve({'commandId': command_id, 'status': 'EXECUTED'})

    assert relay.ack_for('00000000-bbbb-4ccc-8ddd-eeeeeeeeeeee') is None
    assert relay.ack_for('00000009-bbbb-4ccc-8ddd-eeeeeeeeeeee') is not None


def test_reject_reason_codes_fit_the_contract_length_limit():
    """`reasonCode`는 64자 이하다. 넘으면 백엔드가 본문을 거부한다."""
    from sentinel_bridge import command_relay as module

    limit = _load_schema('command-ack.schema.json')['properties']['reasonCode'][
        'maxLength'
    ]
    for code in (
        module.REASON_NOT_IMPLEMENTED,
        module.REASON_MISSION_MANAGER_DOWN,
        module.REASON_MALFORMED_COMMAND,
    ):
        assert 0 < len(code) <= limit, code


def test_relay_rejections_satisfy_the_ack_contract():
    """bridge가 직접 만드는 거부 본문도 계약을 만족해야 한다."""
    jsonschema = pytest.importorskip('jsonschema')

    relay = _relay()
    decision = relay.decide(
        _command_envelope('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee', 'RETURN'),
        mission_manager_alive=True,
    )
    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema('command-ack.schema.json')
        ).iter_errors(decision.ack)
    )
    assert not errors, [error.message for error in errors]


# ----------------------------------------------------------------------
# telemetry의 임무 귀속 (S15P11A301-190)
# ----------------------------------------------------------------------


def test_임무_활성_매핑이_모든_상태를_덮는다():
    """상태가 추가되면 여기서 걸린다.

    누락되면 `.get(..., False)`가 임무 중 궤적을 통째로 버린다. 화면에는
    "안 나온다"로만 보여서 원인을 찾기 어렵다.
    """
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'sentinel_mission'))
    try:
        mission_state = importlib.import_module('sentinel_mission.mission_state')
    except ImportError:
        import pytest as _pytest

        _pytest.skip('sentinel_mission이 없다. 같은 워크스페이스에서만 검사한다')

    from sentinel_bridge.message_mapper import MISSION_ACTIVE_BY_STATE

    missing = [
        state.value
        for state in mission_state.MissionState
        if state.value not in MISSION_ACTIVE_BY_STATE
    ]
    assert not missing, f'임무 활성 매핑이 없는 상태: {missing}'
    assert all(isinstance(v, bool) for v in MISSION_ACTIVE_BY_STATE.values())


def test_임무_밖_상태는_귀속시키지_않는다():
    """종료 후 telemetry가 완료된 임무의 궤적에 섞이면 안 된다.

    `/mission/status`가 TRANSIENT_LOCAL이라 COMPLETED가 missionId를 담은 채
    계속 남아 있다(S15P11A301-171). 상태로 걸러야 하는 이유다.
    """
    from sentinel_bridge.message_mapper import active_mission_id

    mid = '11111111-2222-3333-4444-555555555555'
    assert active_mission_id({'state': 'COMPLETED', 'missionId': mid}) is None
    assert active_mission_id({'state': 'SAFE_IDLE', 'missionId': mid}) is None


def test_임무_중_상태는_귀속시킨다():
    from sentinel_bridge.message_mapper import active_mission_id

    mid = '11111111-2222-3333-4444-555555555555'
    for state in ('EXPLORING', 'INTERACTING', 'PAUSED', 'MANUAL', 'ESTOP'):
        assert active_mission_id({'state': state, 'missionId': mid}) == mid, state


def test_상태가_없거나_모르면_귀속시키지_않는다():
    """오염이 누락보다 고치기 어렵다 — 그럴싸한 궤적 안에 섞여 안 보인다."""
    from sentinel_bridge.message_mapper import active_mission_id

    mid = '11111111-2222-3333-4444-555555555555'
    assert active_mission_id(None) is None
    assert active_mission_id({}) is None
    assert active_mission_id({'state': 'EXPLORING'}) is None
    assert active_mission_id({'state': '새로운상태', 'missionId': mid}) is None
    assert active_mission_id({'state': 'EXPLORING', 'missionId': None}) is None


def test_telemetry_봉투에_missionId가_실린다():
    """비면 robot_pose.mission_id가 null이 되어 관제가 조회할 수 없다."""
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema가 없으면 계약 검증을 건너뛴다"
    )
    mapper = MessageMapper('SENTINEL-01')
    mid = '11111111-2222-3333-4444-555555555555'
    envelope = mapper.telemetry(
        pose={'x': 1.0, 'y': 2.0, 'yaw': 0.5, 'mapId': None},
        mission_state='EXPLORING',
        mission_id=mid,
    )
    assert envelope['missionId'] == mid
    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema('envelope.schema.json')
        ).iter_errors(envelope)
    )
    assert not errors, [error.message for error in errors]


# ----------------------------------------------------------------------
# 탐지 노드 생존을 관제에 알린다 (S15P11A301-192)
# ----------------------------------------------------------------------


def test_state의_components에_detector가_실린다():
    """탐지 노드가 죽어도 스택 나머지는 정상 기동한다.

    이 값이 없으면 관제 화면상 정상으로 보인다. 실제로 그 상태로 여러 검증을
    돌린 뒤에야 알아챘다.
    """
    jsonschema = pytest.importorskip(
        "jsonschema", reason="jsonschema가 없으면 계약 검증을 건너뛴다"
    )
    mapper = MessageMapper('SENTINEL-01')
    envelope = mapper.state(
        mission_state='EXPLORING',
        control_mode='AUTO',
        safety_state='RUNNING',
        active_mission_id=None,
        components={'camera': True, 'lidar': True, 'detector': False},
    )
    assert envelope['data']['components']['detector'] is False
    errors = list(
        jsonschema.Draft202012Validator(
            _load_schema('state.schema.json')
        ).iter_errors(envelope['data'])
    )
    assert not errors, [error.message for error in errors]


def test_components는_불리언_맵이라_스키마_변경이_필요없다():
    """state.schema.json의 components는 additionalProperties가 boolean이다.

    health와 다르다 — 그쪽은 additionalProperties: false라서 필드를 늘리려면
    스키마를 함께 고쳐야 한다. detector를 components에 둔 이유다.
    """
    schema = _load_schema('state.schema.json')
    components = schema['properties']['components']
    assert components['additionalProperties'] == {'type': 'boolean'}

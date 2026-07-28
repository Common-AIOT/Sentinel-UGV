#!/usr/bin/env python3
"""젯슨 telemetry 시뮬레이터 (S15P11A301-103).

common/samples 의 검증된 예제를 템플릿으로 삼아 실제 젯슨과 같은 토픽·QoS·Retain 으로
발행한다. 젯슨 실데이터가 없는 동안 백엔드 수집 경로를 검증하는 용도이며, 젯슨 담당자가
접속 설정을 확인할 때도 그대로 쓸 수 있다.

로컬 브로커:
    python scripts/telemetry_sim.py

EC2 브로커(443 WebSocket):
    python scripts/telemetry_sim.py --host api.sentinel-ugv.xyz --port 443 \
        --transport websockets --path /mqtt --tls \
        --username sentinel-01 --password '****'

필요 패키지: paho-mqtt>=2.1
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

# 연결 수립을 기다리는 시간. 인증 실패는 CONNACK 로 오므로 접속 자체는 성공한다.
CONNECT_TIMEOUT_SECONDS = 10

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES = REPO_ROOT / "common" / "samples"
TOPIC_PREFIX = "sentinel/v1/robots"

# 31-4. 채널별 QoS·Retain. 젯슨(mqtt_client.py)과 같은 값이어야 한다.
CHANNEL_POLICY = {
    "presence": (1, True),
    "telemetry": (0, False),
}

_running = True


def utc_now_iso() -> str:
    """봉투의 sentAt 형식. UTC 이며 반드시 Z 로 끝난다(envelope.schema.json)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_sample(name: str) -> dict:
    with (SAMPLES / name).open(encoding="utf-8") as f:
        return json.load(f)


def topic_for(robot_id: str, channel: str) -> str:
    return f"{TOPIC_PREFIX}/{robot_id}/{channel}"


def publish(client: mqtt.Client, robot_id: str, channel: str, message: dict) -> None:
    """발행 결과를 확인한다.

    연결이 없으면 paho 는 조용히 버리고 예외도 던지지 않는다. 확인하지 않으면 인증 실패
    상태에서도 "발행했다"고 출력되어 서버 쪽을 헛되게 뒤지게 된다.
    """
    qos, retain = CHANNEL_POLICY[channel]
    info = client.publish(
        topic_for(robot_id, channel),
        json.dumps(message, ensure_ascii=False),
        qos=qos,
        retain=retain,
    )
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"{channel} 발행 실패: rc={info.rc}")


def presence_message(robot_id: str, status: str, reason: str | None) -> dict:
    sample = load_sample("presence-online.json")
    sample["messageId"] = str(uuid.uuid4())
    sample["robotId"] = robot_id
    sample["sentAt"] = utc_now_iso()
    sample["data"] = {"robotId": robot_id, "status": status, "reason": reason}
    return sample


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="젯슨 telemetry 시뮬레이터")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--transport", choices=["tcp", "websockets"], default="tcp")
    p.add_argument("--path", default="/mqtt", help="WebSocket 경로 (--transport websockets)")
    p.add_argument("--tls", action="store_true", help="TLS 사용 (443/8883)")
    p.add_argument("--username", default="")
    p.add_argument("--password", default="")
    p.add_argument("--robot-id", default="SENTINEL-01")
    p.add_argument("--mission-id", default=None, help="지정하면 봉투의 missionId 에 넣는다")
    p.add_argument("--interval", type=float, default=0.5, help="발행 주기(초). 기본 2Hz")
    p.add_argument("--count", type=int, default=0, help="발행 건수. 0 이면 무한")
    p.add_argument(
        "--sample",
        default="telemetry-esp32-absent.json",
        help="템플릿 예제. 기본값은 ESP32 미연동(현재 실제 형태)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{args.robot_id}-sim",
        protocol=mqtt.MQTTv5,
        transport=args.transport,
    )
    if args.transport == "websockets":
        client.ws_set_options(path=args.path)
    if args.username:
        client.username_pw_set(args.username, args.password)
    if args.tls:
        client.tls_set()

    # 젯슨과 동일하게 LWT 를 등록한다. 이 프로세스를 강제 종료하면 브로커가 대신 발행한다.
    lwt = presence_message(args.robot_id, "OFFLINE", "MQTT_CONNECTION_LOST")
    qos, retain = CHANNEL_POLICY["presence"]
    client.will_set(
        topic_for(args.robot_id, "presence"),
        json.dumps(lwt, ensure_ascii=False),
        qos=qos,
        retain=retain,
    )

    # CONNACK 를 기다렸다가 실패하면 즉시 멈춘다. 인증 실패(비밀번호·ACL)를 여기서 드러낸다.
    connected = threading.Event()
    connect_failure: list[str] = []

    def on_connect(_client, _userdata, _flags, reason_code, _properties=None):
        if reason_code == 0:
            connected.set()
        else:
            connect_failure.append(str(reason_code))
            connected.set()

    client.on_connect = on_connect

    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    if not connected.wait(CONNECT_TIMEOUT_SECONDS):
        client.loop_stop()
        print(f"접속 실패: {args.host}:{args.port} 에서 CONNACK 를 받지 못했다", file=sys.stderr)
        return 1
    if connect_failure:
        client.loop_stop()
        print(
            f"접속 거부: {connect_failure[0]} — 계정·비밀번호를 확인하라",
            file=sys.stderr,
        )
        return 1
    print(f"접속 성공: {args.host}:{args.port} (user={args.username or '익명'})")

    publish(client, args.robot_id, "presence", presence_message(args.robot_id, "ONLINE", None))
    print(f"presence ONLINE 발행: {topic_for(args.robot_id, 'presence')}")

    template = load_sample(args.sample)
    sequence = 0
    sent = 0
    while _running and (args.count == 0 or sent < args.count):
        message = dict(template)
        message["messageId"] = str(uuid.uuid4())
        message["robotId"] = args.robot_id
        message["missionId"] = args.mission_id
        message["sequence"] = sequence
        message["sentAt"] = utc_now_iso()
        publish(client, args.robot_id, "telemetry", message)
        sequence += 1
        sent += 1
        if sent % 10 == 0:
            print(f"telemetry {sent}건 발행")
        time.sleep(args.interval)

    # 정상 종료는 SHUTDOWN 으로 구분된다. DISCONNECT 를 보내면 LWT 는 발행되지 않는다.
    publish(client, args.robot_id, "presence", presence_message(args.robot_id, "OFFLINE", "SHUTDOWN"))
    time.sleep(0.2)
    client.loop_stop()
    client.disconnect()
    print(f"종료. telemetry {sent}건 발행")
    return 0


def _handle_sigint(_signum, _frame) -> None:
    global _running
    _running = False


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_sigint)
    sys.exit(main())

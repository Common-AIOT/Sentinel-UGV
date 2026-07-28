# sentinel_bridge

ROS 토픽을 MQTT로 변환해 관제 서버에 발행합니다 (S15P11A301-128).

명세 **31장**이 규범입니다. 토픽·QoS·Retain은 31-4, 봉투는 31-5, 재연결과 복구
순서는 31-10, 보안과 포트는 31-11을 따릅니다.

> 이전 README는 `WebSocket/REST bridge`라고 적혀 있었습니다. 31-1이 확정한
> 프로토콜은 `MQTT 5 over TLS`이므로 정정했습니다.

## 방향이 한쪽인 이유

SSAFY 망이 인바운드를 막으므로 **젯슨은 항상 발신자**입니다. MQTT도 젯슨이
브로커에 접속하는 outbound 연결입니다. 그래서 브로커는 EC2에 둡니다. 젯슨에 두면
Spring이 젯슨으로 접속해 들어가야 하는데 불가능합니다.

유일한 인바운드는 브라우저가 젯슨에 직접 붙는 WebRTC뿐이고, 그래서 영상만 동일
Wi-Fi 조건이 붙습니다(32-4 LOCAL).

## 발행 채널

| 채널 | QoS | Retain | 주기 | 상태 |
|---|---:|---:|---|---|
| `presence` | 1 | O | 접속·종료·LWT | 구현 |
| `state` | 1 | O | 변경 시 + 1초 | 구현 |
| `telemetry` | 0 | X | 2Hz | 구현 |
| `events` | 1 | X | 탐지·오류 즉시 | S15P11A301-123 |
| `acks` | 1 | X | 명령 응답 | ESP32 연동 이후 |

`cmd/*` 구독은 31-13 2단계이며 ESP32 연동(S15P11A301-84~86)에 묶여 있습니다.

Retain을 `presence`와 `state`에만 쓰는 이유는 그 둘만 "지금 상태"이기 때문입니다.
`telemetry`를 Retain하면 새 구독자가 낡은 값을 현재값으로 봅니다. `cmd/*`는
Retain을 **금지**합니다. 과거 명령이 재연결 직후 실행되면 로봇이 움직입니다(31-4).

## 실행

```bash
ros2 launch sentinel_bridge cloud_bridge.launch.py \
    broker_host:=mqtt.sentinel-ugv.xyz broker_username:=sentinel-01
```

자격증명은 커밋하지 않습니다. launch 인자나 환경변수로 넘깁니다.

## 의존성

`paho-mqtt`가 필요합니다(31-3 권장 클라이언트). ROS 노드는 시스템 파이썬으로
도므로 venv가 아니라 시스템 쪽에 설치해야 합니다.

```bash
sudo apt install -y python3-paho-mqtt                      # 정석
/usr/bin/python3 -m pip install --user "paho-mqtt>=2.1"    # sudo 없이
```

## 미확보 필드는 null입니다

ESP32 연동 전에는 젯슨이 알 수 없는 값이 있습니다. **스키마는 31-6 전체 형태를
유지하고 미확보 필드만 null로 보냅니다.** 나중에 필드를 추가하면 백엔드 파싱과
DB 스키마, 프런트엔드 표시를 다시 건드려야 합니다.

| 지금 실제 값 | ESP32 연동 후 |
|---|---|
| `compute` (CPU·GPU·메모리·온도) | `environment` (DHT11 온습도) |
| `health.lidarOk`, `health.cameraOk` | `battery` |
| | `motion` (엔코더) |
| | `health.mcuConnected` |

`null`과 `false`는 다릅니다. `health.mcuConnected`가 `false`면 확인했고 끊긴
것이고 `null`이면 확인할 수단이 없는 것입니다. 관제 화면이 "장애"와 "미구현"을
구분해야 하므로 섞지 않습니다.

## 계약 검증

```bash
python3 scripts/ci/validate_schemas.py                          # 스키마와 예제
python3 -m pytest jetson/ros2_ws/src/sentinel_bridge/test -q    # 코드가 만드는 메시지
```

CI의 `test:message-contract`가 둘 다 돌립니다. 브로커도 ROS도 필요 없습니다.
`message_mapper`와 `mqtt_client`가 `rclpy`를 import하지 않도록 만들었기
때문입니다.

## 검증 기록 (2026-07-28)

젯슨에 Mosquitto 2.0.11을 설치해 **MQTT 5로** 검증했습니다. EC2 브로커는
S15P11A301-103에서 만들지만, 젯슨 쪽은 여기서 확정했습니다.

```text
MQTT 5 연결   기본 경로(protocol_version=5)로 접속 확인
발행 QoS      presence 1 / state 1 / telemetry 0   31-4와 일치
발행 주기     presence 1건, state 1.33Hz, telemetry 2.00Hz
Retain        presence·state만 Retain. 새 구독자가 즉시 수신
LWT           SIGKILL 시 브로커가 OFFLINE(MQTT_CONNECTION_LOST) 발행
정상 종료     SIGINT은 OFFLINE(SHUTDOWN)으로 구분됨
브로커 부재   노드 생존, 지수 백오프 재시도
브로커 복구   자동 재연결 후 복구 순서(presence → state) 준수
TLS + 인증    8883 상당 리스너에 자체 서명 인증서로 접속 성공
스키마        발행 메시지 전부 common/schemas 통과
단위 시험     18건 통과. 결함 주입 시 정상적으로 실패
```

### 31-11 보안 정책 실측

```text
익명 접속           거부
잘못된 비밀번호      거부
TLS 없이 평문 접속   거부
인증서 검증 실패     거부
자기 telemetry 발행  허용
다른 차량 토픽 발행  차단 (메시지 미도착)
자기 cmd/* 발행      차단 (메시지 미도착)
다른 차량 토픽 구독  차단 (메시지 미수신)
자기 cmd/* 구독      허용
Spring 계정 전체     허용
```

참고 설정은 `config/mosquitto.conf.example`과 `config/mosquitto-acl.example`에
있습니다. EC2 적용은 S15P11A301-103입니다.

> **ACL 시험에서 종료 코드를 믿지 마세요.** mosquitto는 ACL로 거부한 발행을
> **조용히 버리고 클라이언트에는 성공을 알립니다.** ACL 구조를 노출하지 않으려는
> 설계입니다. QoS 1과 MQTT 5로 올려도 마찬가지입니다.
>
> 처음에 `mosquitto_pub`의 종료 코드로 판정해 "ACL이 동작하지 않는다"고
> 오판했습니다. **메시지가 실제로 도착하는지만이 진실을 말합니다.** 구독자를
> 붙여 확인하세요.

## 문제 해결

### `ros2 run`이 패키지를 못 찾는다

`colcon list`로 타입을 확인합니다.

```bash
colcon list | grep sentinel_bridge
#   (ros.ament_python) 이어야 정상
#   (python) 이면 package.xml을 colcon이 읽지 못한 것
```

`(python)`이면 `package.xml`이 XML로 파싱되지 않는 상태입니다. **colcon은 파싱
실패를 조용히 넘기고** 일반 python 패키지로 취급하므로 빌드는 성공하지만
`ament_prefix_path` 훅이 생기지 않습니다.

```bash
python3 -c "import xml.etree.ElementTree as ET; ET.parse('package.xml')"
```

가장 흔한 원인은 **주석 안의 연속된 하이픈 두 개**입니다. XML 주석에는 쓸 수
없는데 명령줄 옵션을 주석에 적으면 걸립니다. 실제로 겪었습니다.

### 로컬 검증용 브로커

Mosquitto를 젯슨에 깔아 씁니다. 배포 대상이 아니라 개발용입니다.

```bash
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl disable --now mosquitto   # 상시 1883 서비스는 쓰지 않는다
mosquitto -c <설정파일>                   # 높은 포트에 직접 띄운다
```

`mosquitto-clients`의 `mosquitto_pub`/`mosquitto_sub`가 "젯슨이 안 보내는 건가
서버가 못 받는 건가"를 가릴 때 계속 쓰입니다.

`protocol_version` 파라미터는 MQTT 3.1.1로 내릴 때만 씁니다. 순수 파이썬
브로커(`amqtt`)로 시험하면 필요합니다. 그것은 3.1.1까지만 지원해서 MQTT 5로
접속하면 CONNECT를 잘못 해석해 LWT를 잃습니다.

```text
Failed to initialize client session: Will flag set, but will topic/message not present
```

또 `amqtt`는 수신 QoS를 **구독 QoS로 올려 보고**하므로 발행 QoS 검증에 쓸 수
없습니다. Mosquitto는 `min(발행, 구독)` 의미를 지켜 역산이 가능합니다.

어느 브로커를 쓰든 `test/test_message_contract.py`가 발행 호출을 직접 붙잡아
확인하므로 브로커에 의존하지 않는 방어선이 하나 더 있습니다.

### 라이다 상태가 항상 null이다

`/scan` 발행자는 BEST_EFFORT입니다. RELIABLE로 구독하면 메시지를 하나도 받지
못하고 다음 경고가 뜹니다.

```text
New publisher discovered on topic '/scan', offering incompatible QoS.
No messages will be received from it. Last incompatible policy: RELIABILITY
```

이 노드는 BEST_EFFORT로 구독합니다. RELIABLE 발행자(usb_cam)도 BEST_EFFORT
구독자에게는 보낼 수 있으므로 양쪽 다 받습니다.

### 브로커가 없을 때

노드는 죽지 않고 지수 백오프(1, 2, 4, 8, 최대 30초)로 계속 재시도합니다. 그동안
발행은 조용히 버립니다. 관제 링크가 카메라·스트리밍·AI를 막으면 안 됩니다
(32장 장애 격리).

재전송이 필요한 이벤트는 `outbox_repository`가 담당합니다. `telemetry`는 큐에
쌓지 않습니다. 31-10이 "긴 backlog 금지"를 명시했고, 복구 직후 낡은 값이
쏟아지면 관제가 과거를 현재로 봅니다.

# Scripts

실제로 쓰는 것만 둔다. 쓰지 않는 스크립트는 헷갈림만 만들기 때문에 남기지
않는다(S15P11A301-125).

## Jetson 설치

| 스크립트 | 하는 일 |
|---|---|
| `setup_jetson.sh` | 툴체인 확인, `usb_cam` 필수 패치 멱등 적용, MediaMTX 설치 |
| `gen_stream_cert.sh` | WebRTC HTTPS용 자체 서명 인증서 생성 |

```bash
./scripts/setup_jetson.sh            # 설치·패치 적용
./scripts/setup_jetson.sh --check    # 적용 여부만 확인 (미적용이면 실패)
./scripts/gen_stream_cert.sh         # ~/.config/sentinel/certs 에 생성
```

패치를 손으로 `git apply` 하지 않는다. 목록은 `setup_jetson.sh`의
`required_patches`가 관리하며 사유는
[`jetson/ros2_ws/patches/README.md`](../jetson/ros2_ws/patches/README.md)에 있다.

## 젯슨 ROS 스택 실행

| 스크립트 | 하는 일 |
|---|---|
| `start_sentinel.sh` | 중복 검사 → 센서 → 토픽 확인 → 스트리밍·MediaMTX |
| `stop_sentinel.sh` | launch 트리와 노드를 정리하고 남은 프로세스를 확인 |
| `demo_up.sh` | 센서·SLAM·스트리밍·녹화·임무·브리지·탐지를 순차 기동 |

이름이 `start_streaming`이 아닌 이유는 켜는 대상이 스트리밍만이 아니기
때문이다. 지금은 센서와 스트리밍이고 여기에 녹화(S15P11A301-123)와 AI·임무
노드가 붙는다. 명세 37-3의 systemd 유닛도 `sentinel-*` 접두어이므로 나중에
서비스화할 때 이름이 그대로 이어진다.

설치(`setup_jetson.sh`)는 이 기계를 개발 가능 상태로 만드는 1회 작업이고,
실행(`start_sentinel.sh`)은 매번 하는 작업이다. 두 축을 섞지 않는다.

```bash
./scripts/start_sentinel.sh                  # HTTPS (기본)
./scripts/start_sentinel.sh --no-tls         # 평문 HTTP
./scripts/start_sentinel.sh --sensors-only   # 센서만, 스트리밍 없이
./scripts/stop_sentinel.sh

# 데모 전체 스택. 개별 기능은 launch 인자로 끌 수 있다.
./scripts/demo_up.sh
./scripts/demo_up.sh enable_detector:=false
./scripts/demo_up.sh enable_viz:=true        # Foxglove 시각화까지 (아래 참고)
```

`start_sentinel.sh`는 센서·스트리밍 경로를 빠르게 확인하는 개발용 진입점이고,
`demo_up.sh`는 실제 데모 구성을 한 번에 올리는 진입점이다. 둘을 동시에 실행하면
카메라와 MediaMTX 발행자가 중복되므로 먼저 실행한 쪽을 종료하고 전환한다.

### 부팅 자동 시작

`scripts/systemd/sentinel-demo.service`는 `demo_up.sh`를 부팅 시 실행한다. 개발
중에는 수동 launch와 이중 실행되지 않도록 비활성으로 두고, 재부팅 복구까지
검증하거나 데모를 운영할 때만 켠다.

```bash
sudo cp scripts/systemd/sentinel-demo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo loginctl enable-linger orin
sudo systemctl enable --now sentinel-demo

# 개발로 돌아갈 때
sudo systemctl disable --now sentinel-demo
```

서비스 로그는 `journalctl -u sentinel-demo -f`로 확인한다. 유닛의 `ExecStart`는
`/home/orin/projects/S15P11A301`을 기준으로 하므로 저장소 경로가 다르면 설치
전에 해당 줄을 맞춰야 한다.

### 시각화(Foxglove) 켜고 끄기

SLAM 지도·스캔·TF를 눈으로 보는 수단이다(S15P11A301-177). **기본은 꺼져 있다.**

```bash
./scripts/viz_up.sh            # 켠다. 돌고 있는 스택에 붙는다
./scripts/viz_down.sh          # 끈다. 스택은 그대로 둔다

./scripts/viz_up.sh --local    # 127.0.0.1 만 — 외부 노출 없이 SSH 터널로 본다
```

두 스크립트 다 두 번 연속 불러도 안전하다. 이미 켜져 있으면 두 번째를 띄우지
않고(포트 바인딩 실패로 로그만 헷갈려진다), 이미 꺼져 있으면 그대로 성공한다.

`viz_up.sh`는 백그라운드로 띄우므로 터미널이 묶이지 않는다. 로그는
`/tmp/sentinel-viz.log`이고, 8765가 실제로 열릴 때까지 기다린 뒤 결과를 낸다 —
"띄웠다"만 출력하면 실패해도 성공처럼 보인다.

`viz_down.sh`는 `pkill -f foxglove_bridge`를 쓰지 않는다. 그 패턴이 호출한 셸의
명령줄에 들어 있어 셸 자신이 함께 죽는다(S15P11A301-125에서 `stop_sentinel.sh`가
같은 사고를 겪었다). PID를 먼저 모으고 자기 자신과 부모를 제외한다.

**스택을 처음 띄울 때 함께 켜는 길도 있다**(`./scripts/demo_up.sh enable_viz:=true`).
다만 이미 스택이 돌고 있으면 `viz_up.sh`를 쓴다. 스택을 재시작하면 그때까지 쌓은
SLAM 지도를 잃는다 — 지도는 메모리에만 있고 임무 종료 시점에 저장되므로
(S15P11A301-171) 중간에 재시작하면 처음부터 다시 그린다.

접속은 `ws://jetson.sentinel-ugv.xyz:8765`이고 연결 유형은 반드시
**"Foxglove WebSocket"** 이다. Rosbridge를 고르면 핸드셰이크가 깨지고 서버 로그에
`Dropping client ...: handshake`가 남는다.

#### 3D 패널에 지도가 안 보이면 Display frame부터 본다

토픽을 켜도 빈 격자만 보이는 경우가 대부분 이것이다.

1. 3D 패널 우측 상단 **⚙** → **Frame → Display frame**을 `map`으로 바꾼다.
   기본값이면 지도가 로봇과 함께 회전해 읽을 수 없다.
2. Topics에서 `/map`(점유격자), `/scan`(현재 스캔), `/robot_description`을 켠다.
3. 화면이 비어 보이면 우클릭 → Focus 또는 fit 버튼으로 시점을 맞춘다.

`/map`과 `/scan`을 함께 켜면 정합을 판정할 수 있다. 현재 스캔이 지도의 벽 위에
겹치면 정상이고, 밀려 있거나 긴 벽이 **두 겹으로** 보이면 위치 추정이 틀어진
것이다. 명세의 「LiDAR 검증」이 같은 기준을 쓴다 — "긴 평면 벽이 휘거나 두 겹으로
보이지 않는지 확인한다"(docs/03-제어-캘리브레이션.md), 합격 기준 CAL-06.

명세는 이 확인을 RViz로 적었지만 이 젯슨에 `rviz2`가 설치돼 있지 않다(ROS가
`ros-base`로 깔려 GUI 패키지가 빠져 있다). 판정 기준은 도구와 무관하므로
Foxglove의 같은 패널로 본다.

#### 켠 상태는 인증이 없다

`foxglove_bridge`는 8765를 인증 없이 열고, 기본 capabilities에 `clientPublish`·
`parameters`·`services`가 들어 있다 — 접속만 하면 파라미터를 바꾸고 서비스를
부를 수 있다. 젯슨이 공인 IP에 있어 LAN과 인터넷이 같은 인터페이스이므로
"LAN에서만 열기"라는 선택지가 없다.

그래서 **볼 때만 켜고 보고 나면 `viz_down.sh`로 끈다.** 외부에 아예 열지 않으려면
`--local`로 켜고 노트북에서 터널을 연다.

```bash
ssh -N -L 8765:127.0.0.1:8765 orin@<젯슨 주소>
# 그다음 Foxglove 에서 ws://localhost:8765
```

#### enable_viz를 줬는데도 안 뜨면 재빌드부터 확인한다

```text
[launch.user]: [demo.launch] sentinel_bringup/viz.launch.py 가 없어 건너뛴다.
```

이 줄이 보이면 인자 문제가 아니라 **설치본에 launch 파일이 없는 것**이다.
`--symlink-install`이라도 **새 파일은 다시 빌드해야 심링크가 생긴다.** 기존 파일
수정만 즉시 반영된다.

```bash
colcon build --symlink-install --packages-select sentinel_bringup \
  --base-paths jetson/ros2_ws/src \
  --build-base jetson/ros2_ws/build --install-base jetson/ros2_ws/install
```

이 증상이 헷갈리는 이유는 `demo.launch.py`가 없는 파일을 만나면 로그만 남기고
넘어가도록 만들어져 있어서다(S15P11A301-156의 장애 격리). **스택이 정상 기동하므로
인자가 안 먹은 것처럼 보인다.** 브랜치를 옮긴 뒤에 재발할 수 있다.

#### 8765는 인증이 없고 이 기기는 공인 IP에 있다

작업이 끝나면 내린다.

```bash
pkill -f foxglove_bridge
```

이 기기는 NAT 뒤가 아니다. `jetson.sentinel-ugv.xyz`가 이 기기를 직접 가리키고
임의 포트가 외부에서 닿는다(WHEP 8889가 그렇게 동작한다). 그런데
`foxglove_bridge`의 기본 capability는 읽기만이 아니다.

```text
[clientPublish, parameters, parametersSubscribe, services, connectionGraph, assets]
```

**토픽 발행·서비스 호출·파라미터 변경이 되고 인증이 없다.** 켜 둔 동안은 누구나
로봇을 조작할 수 있다는 뜻이다. `viz_address` 기본값도 `0.0.0.0`이다.

포트를 열지 않고 쓰려면 localhost로 묶고 SSH 터널을 쓴다.

```bash
ros2 launch sentinel_bringup viz.launch.py viz_address:=127.0.0.1
ssh -L 8765:localhost:8765 orin@jetson.sentinel-ugv.xyz   # 노트북에서
# Foxglove 접속 주소는 ws://localhost:8765
```

`demo_up.sh`에 `enable_viz:=true`를 박아 두지 않는 이유가 이것이다. 이 스크립트는
systemd가 부팅마다 부르는 진입점이므로(위 「부팅 자동 시작」), 박아 두면 무인
상태로 이 포트가 상시 열린다.

### 센서는 카메라만이 아니다

`sensors.launch.py`가 `lidar.launch.py`를, 그것이 다시 `description.launch.py`를
include하므로 **한 번에 셋이 뜬다.**

| 노드 | 발행 |
|---|---|
| `usb_cam` | `/camera/image_raw/compressed` |
| `ydlidar_ros2_driver_node` | `/scan` |
| `robot_state_publisher` | `/tf`, `/tf_static`, `/robot_description` |

라이다를 따로 켜는 명령은 없다. 스트리밍만 필요해도 라이다가 함께 뜬다.
`/scan`이 안 올라오면 스크립트가 경고하지만 실패로 다루지는 않는다. 라이다가
없어도 스트리밍은 되기 때문이다.

### 중복 검사가 핵심이다

`start_sentinel.sh`는 **이미 실행 중이면 거부한다.** MediaMTX는 한 경로에
발행자 하나만 허용하므로 `stream_pipeline`이 두 개 뜨면 서로 경로를 빼앗으며
재구성을 반복하고, 증상이 네트워크 문제처럼 보인다.

`robot_state_publisher`도 함께 검사한다. 정리 목록에서 빠져 있던 동안
start/stop을 돌 때마다 고아가 하나씩 쌓여 3개까지 누적됐다. 그것들이 같은 TF를
중복 발행하므로 하위 노드가 흔들린다.

### 프로세스를 셀 때 pgrep을 쓰지 않는다

`pgrep -f stream_pipeline`은 패턴 문자열을 명령줄에 가진 **자기 셸까지 세어**
항상 1 이상을 반환한다. `pkill -f`는 같은 이유로 자기 자신을 죽인다. 이 조사
중에도 세 번 걸렸다. 손으로 확인할 때는 다음을 쓴다.

```bash
ps -eo pid=,cmd= | grep -F "lib/sentinel_streaming/stream_pipeline" | grep -v grep
ros2 node list                      # ROS 노드 기준으로는 이것이 정확하다
```

브라우저는 `127.0.0.1`이 아니라 스크립트가 출력하는 실제 IP로 접속한다. VS
Code 포트 포워딩된 주소로 열면 WHEP 요청이 막힌다.

### 관제 웹은 감싸지 않는다

```bash
cd frontend && npm run dev
```

`next dev`는 이미 모든 인터페이스(`*:3000`)에 바인딩하므로 LAN에서 바로 열린다.
`-H 0.0.0.0` 같은 인자가 필요 없고, 감싸는 스크립트를 두면 표준 명령을 한 겹
가리기만 한다.

대신 `start_sentinel.sh`가 `frontend/.env.local`의
`NEXT_PUBLIC_LOCAL_STREAM_URL`을 현재 IP와 **대조해 경고한다.** 그 값은 IP가
박혀 있어서 DHCP가 주소를 바꾸면 화면이 "연결 중"에서 멈추고 서버 로그에는
아무것도 남지 않는다. 원인을 찾기 어려운 종류라 미리 잡는다.

값을 고친 뒤에는 dev 서버를 다시 시작해야 한다. Next.js는 `NEXT_PUBLIC_` 값을
빌드 시점에 코드에 넣기 때문이다.

## CI

| 스크립트 | 하는 일 |
|---|---|
| `ci/validate_repository.sh` | 모노레포 필수 경로·파일, README 동기화, 최상위 산출물 검사 |
| `ci/validate_schemas.py` | `common/schemas` 자체와 `common/samples` 예제가 봉투·본문 스키마를 만족하는지 |
| `ci/run_contract_tests.sh` | `test:message-contract` job을 로컬에서 그대로 돌린다 |
| `ci/contract-test-requirements.txt` | 그 job이 쓰는 파이썬 패키지 목록. CI와 위 스크립트가 같은 파일을 읽는다 |

`.gitlab-ci.yml`의 `lint` 스테이지가 매 파이프라인에서 실행한다. 이 문서에
없는 스크립트가 `scripts/`에 있으면 실패하므로, 스크립트를 추가하면 위 표에도
함께 적는다. 검사 범위는 `scripts/*.sh`이며 `scripts/ci/` 아래는 자동 검사되지
않지만 같은 이유로 여기 적는다.

### 계약 시험은 푸시 전에 로컬에서

```bash
./scripts/ci/run_contract_tests.sh
```

젯슨에서 그냥 `pytest`를 돌리면 통과하는데 CI에서 깨지는 일이 반복됐다. 원인은
환경 차이 두 가지다.

1. 젯슨에는 ROS가 source돼 있어 `PYTHONPATH`로 `rclpy`가 보인다
2. 시스템 파이썬에 `requests`·`numpy` 같은 것이 이미 깔려 있다

CI 컨테이너(`python:3.10-alpine`)에는 둘 다 없다. 위 스크립트가 전용 venv와
`env -i`로 그 차이를 없앤다. 실제로 이것을 만들기 전에 같은 원인으로 CI를 두 번
깼다.

## 여기 없는 것

- **백엔드·DB 운영 스크립트**: `backup.sh`, `health_check.sh`,
  `check_dev_environment.sh`/`.ps1`을 두었으나 아무도 쓰지 않아 삭제했다.
  필요해지면 git 이력에서 되살린다.
- **`deploy_jetson.sh`**: `colcon build` + `colcon test`를 하던 스크립트다.
  테스트가 0개라 항상 통과하면서 "tests passed"를 출력하는 거짓 신호였다.
  E-Stop 확인은 빌드가 아니라 실제 주행 시작에 걸려야 한다.

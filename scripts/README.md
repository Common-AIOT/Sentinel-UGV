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
```

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

`.gitlab-ci.yml`의 `lint` 스테이지가 매 파이프라인에서 실행한다. 이 문서에
없는 스크립트가 `scripts/`에 있으면 실패하므로, 스크립트를 추가하면 위 표에도
함께 적는다.

## 여기 없는 것

- **백엔드·DB 운영 스크립트**: `backup.sh`, `health_check.sh`,
  `check_dev_environment.sh`/`.ps1`을 두었으나 아무도 쓰지 않아 삭제했다.
  필요해지면 git 이력에서 되살린다.
- **`deploy_jetson.sh`**: `colcon build` + `colcon test`를 하던 스크립트다.
  테스트가 0개라 항상 통과하면서 "tests passed"를 출력하는 거짓 신호였다.
  E-Stop 확인은 빌드가 아니라 실제 주행 시작에 걸려야 한다.
- **systemd 유닛**: **MVP 범위 외로 결정했다**(TBD-OPS-001). MVP가 자동 복구를
  구현하지 않으므로 `Restart` 정책의 이점이 사라지고, 남는 것은 부팅 시 자동
  시작 편의뿐이다. 개발 중에는 `journalctl`과 `Restart`가 오히려 디버깅을
  방해한다. 젯슨이 재부팅되면 사람이 `start_sentinel.sh`를 다시 실행한다
  (37-8 Runbook). 안전은 ESP32 watchdog이 담보하므로 영향이 없다.

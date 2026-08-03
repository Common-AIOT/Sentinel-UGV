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
| `demo_up.sh` | 센서·SLAM·스트리밍·녹화·임무·브리지·탐지를 순차 기동 |
| `demo_down.sh` | 그 스택을 전부 내리고 **남은 프로세스를 확인한다** |
| `start_sentinel.sh` | 중복 검사 → 센서 → 토픽 확인 → 스트리밍·MediaMTX |
| `stop_sentinel.sh` | 센서·스트리밍만 정리 (`start_sentinel.sh`의 짝) |
| `viz_up.sh` | 돌고 있는 스택에 Foxglove Bridge를 붙인다 |
| `viz_down.sh` | Bridge만 떼고 스택은 그대로 둔다 |
| `ros_env.sh` | **source 전용.** ROS 소싱과 DDS 격리 설정이 있는 유일한 곳 |

**`stop_sentinel.sh`로 데모 스택을 내릴 수 없다.** 이름 때문에 그렇게 보이지만
그것은 `start_sentinel.sh`의 짝이고 센서·스트리밍만 덮는다. 데모 스택은
`demo_down.sh`를 쓴다. 자세한 경계는 아래 「내릴 때 무엇이 남는가」에 있다.

이름이 `start_streaming`이 아닌 이유는 켜는 대상이 스트리밍만이 아니기
때문이다. 지금은 센서와 스트리밍이고 여기에 녹화(S15P11A301-123)와 AI·임무
노드가 붙는다. 명세 37-3의 systemd 유닛도 `sentinel-*` 접두어이므로 나중에
서비스화할 때 이름이 그대로 이어진다.

설치(`setup_jetson.sh`)는 이 기계를 개발 가능 상태로 만드는 1회 작업이고,
실행(`start_sentinel.sh`)은 매번 하는 작업이다. 두 축을 섞지 않는다.

```bash
# 데모 전체 스택. 개별 기능은 launch 인자로 끌 수 있다.
./scripts/demo_up.sh
./scripts/demo_up.sh enable_detector:=false
./scripts/demo_up.sh enable_viz:=false      # Foxglove Bridge 없이 (아래 참고)
./scripts/demo_down.sh                       # 전부 내린다
./scripts/demo_down.sh --dry-run             # 무엇을 정리할지만 본다

# 센서·스트리밍만 (개발용)
./scripts/start_sentinel.sh                  # HTTPS (기본)
./scripts/start_sentinel.sh --no-tls         # 평문 HTTP
./scripts/start_sentinel.sh --sensors-only   # 센서만, 스트리밍 없이
./scripts/stop_sentinel.sh
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

SLAM 지도·스캔·TF를 눈으로 보는 수단이다(S15P11A301-177).

**기본이 켜져 있다 (S15P11A301-224).** 관제 웹의 실시간 지도가 이 bridge에서
`/map`을 받으므로 더 이상 개발 도구가 아니라 제품 구성요소다. `demo_up.sh`가 함께
띄우므로 보통은 아래 스크립트를 쓸 일이 없다 — 스택을 재시작하지 않고 붙이거나
뗄 때만 쓴다.

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

`demo_up.sh`가 이미 띄우므로 **껐다 켤 때만** 이 스크립트를 쓴다. 스택 전체를
재시작하면 그때까지 쌓은 SLAM 지도를 잃는다 — 지도는 메모리에만 있고 임무 종료
시점에 저장되므로(S15P11A301-171) 중간에 재시작하면 처음부터 다시 그린다.

#### 접속 주소는 wss다

```text
wss://jetson.sentinel-ugv.xyz:8765
```

`ws://`가 아니다(S15P11A301-224). 관제 웹이 HTTPS이므로 평문이면 브라우저가 혼합
콘텐츠로 차단한다. 인증서는 WHEP이 쓰는 것과 같은 파일이며
`jetson.sentinel-ugv.xyz` 이름으로 발급돼 있다.

연결 유형은 반드시 **"Foxglove WebSocket"** 이다. Rosbridge를 고르면 핸드셰이크가
깨지고 서버 로그에 `Dropping client ...: handshake`가 남는다.

개발용으로 평문이 필요하면 `viz_up.sh viz_tls:=false`로 띄운다. 그 상태로는 관제
웹의 지도가 나오지 않는다.

#### 광고되는 토픽이 여섯 개뿐이다

```text
/map  /pose  /scan  /tf  /tf_static  /robot_description
```

`viz_topic_whitelist`의 기본값이다. 카메라 원본은 관제 웹에서 보므로 뺐다. 넓히려면
인자로 준다.

**이 제한이 부하 문제를 없앤다.** bridge를 켜기 전에는 전체 토픽을 직렬화해 CPU
경합이 우려됐는데(S15P11A301-131의 오디오 손실), 여섯 개로 줄인 뒤 실측하니 탐지
FPS 중앙값이 5.90(켜짐) 대 5.80(꺼짐)이었다 — 차이가 잡음 범위이고 부호도 반대다.

#### 쓰기가 막혀 있다

`viz_capabilities` 기본값이 `[connectionGraph]`다. bridge 기본값에는
`clientPublish`·`services`·`parameters`가 들어 있어 접속만 하면 토픽을 발행하고
서비스를 부르고 파라미터를 바꿀 수 있었다. 지금은 읽기만 된다.

**읽기는 열려 있다.** 젯슨이 공인 IP에 있어 인터넷에서 지도와 로봇 위치를 읽을 수
있다. 학생 프로젝트 범위에서 수용한 결정이다. 닫으려면 `viz_up.sh --local`로 띄우고
SSH 터널을 쓴다(그때는 관제 웹의 지도도 안 나온다).

#### 브라우저 첫 접속이 조금 느릴 수 있다

`foxglove_bridge`가 인증서 체인에서 **leaf 하나만 보낸다**(파일에는 3개가 있고
같은 파일을 쓰는 MediaMTX는 전부 보낸다 — bridge 구현 한계다). 브라우저는 leaf의
AIA 주소에서 중간 인증서를 스스로 받아 채운다.

```text
CA Issuers - URI:http://yr1.i.lencr.org/    (HTTP 200, 필요한 중간 인증서)
```

그래서 브라우저는 동작하지만 **AIA를 하지 않는 클라이언트는 실패한다**(예: 파이썬
`ssl` 기본 설정). 시연 망에서 `lencr.org`로 나가는 HTTP가 막히면 인증서 검증이
깨지므로, 그때는 앞단에 TLS 종단(nginx 등)을 두는 것이 대안이다.

#### 3D 패널 설정 (S15P11A301-221)

기본 상태로는 읽을 수 없다. 아래 네 단계를 거치면 다섯 가지만 남는다.

**1. Display frame을 `map`으로**

3D 패널 우측 상단 **⚙** → **Frame → Display frame**. 기본값이면 지도가 로봇과 함께
회전해 읽을 수 없다. 토픽을 다 켰는데 빈 격자만 보이는 경우가 대부분 이것이다.

**2. 켤 토픽 세 개**

| 토픽 | 보이는 것 | 설정 |
|---|---|---|
| `/map` | 점유격자 | 기본값 |
| `/scan` | 현재 라이다 스캔 | Point size 5, 빨강 |
| `/pose` | 로봇 위치와 방향 | **Type: Arrow**, **Covariance: Off** |

`/pose`를 Axis로 두면 축 3개를 그려서 TF 좌표축과 겹쳐 보인다. Arrow 하나가
"여기 있고 이쪽을 본다"를 분명하게 말한다. Covariance는 끈다 — 실측 오차가
3~5cm인데 화면에는 크게 그려져 위치가 불확실한 것처럼 오해를 만든다.

**3. 끌 것**

```text
/slam_toolbox/graph_visualization   포즈 그래프. 디버깅용
/slam_toolbox/scan_visualization    LaserScan 이고 /scan 과 같은 내용. 중복
/camera/image_raw* (4개)            3D 패널에서 볼 것이 아니다
Custom layers → URDF                삭제. 아래 「빨간 오류 두 개」 참고
```

**4. TF 이름표 정리 — 영어 글자를 없애는 곳**

`Transforms` 섹션에 프레임이 12개 있다. 조인트 offset이 대부분 0이라
`ultrasonic_front_link`·`lidar_link`·`camera_link`·`imu_link` 라벨이 **한 점에
겹쳐** 읽을 수 없는 글자 뭉치가 된다.

- 간단: 프레임별 눈 아이콘으로 `base_footprint`·`lidar_link`만 남긴다.
- 더 깔끔: `Transforms` 섹션 자체를 끈다. 위치는 `/pose` 화살표가 대신 보여준다.

#### 화면 요소가 각각 무엇인가

색만 보고는 구분할 수 없는 것이 있다.

| 화면 | 무엇 |
|---|---|
| 회색 | 미탐색 — 라이다 빔이 닿은 적 없는 영역 |
| 흰색 | 탐색된 자유 공간 |
| 검정 | 벽 |
| **작은** 빨간 점 | 현재 스캔 (`/scan`) |
| **큰** 빨간 구 (0.1m) | 포즈 그래프 노드 (`/slam_toolbox/graph_visualization`) |
| 흰 상자 안 영어 | TF 프레임 이름표 |
| 갈색·초록 화살표 | TF 프레임 좌표축 (X=빨강, Y=초록) |

**빨강이 두 종류다.** 크기로만 구분되므로 포즈 그래프를 끄는 것이 낫다.

#### 빨간 오류 두 개는 정상이다

고치려고 시간을 쓰지 않는다.

**URDF 커스텀 레이어** — `sentinel.urdf`는 TF 골격 전용이라 `visual`·`collision`이
없다(링크 13, 조인트 12, 형상 0). 파일 주석이 이유를 적고 있다 — 조인트 origin이
대부분 0이라 geometry를 넣으면 링크가 한 점에 겹쳐 보여 검증에 도움이 되지 않고,
35-7 실측(TBD-HW-006) 반영 후 추가한다. 그릴 것이 없으니 오류가 정상이며 로봇
몸체는 이 방법으로 볼 수 없다.

**`/slam_toolbox/graph_visualization`** — 마커 128개 중 1개가 `ns=''`,
`frame_id=''`, `scale=0.0`, `rgb=(0,0,0)`이다. `frame_id`가 비면 어느 좌표계에 그릴지
알 수 없어 Foxglove가 오류를 낸다. **slam_toolbox 상류 문제이고 우리 코드가
아니다.** 나머지 127개는 정상 표시된다.

#### 정합 판정

`/map`과 `/scan`을 함께 켜면 위치 추정이 맞는지 볼 수 있다. 현재 스캔이 지도의 벽
위에 겹치면 정상이고, 밀려 있거나 긴 벽이 **두 겹으로** 보이면 틀어진 것이다.
명세의 「LiDAR 검증」이 같은 기준을 쓴다 — "긴 평면 벽이 휘거나 두 겹으로 보이지
않는지 확인한다"(docs/03-제어-캘리브레이션.md), 합격 기준 CAL-06.

명세는 이 확인을 RViz로 적었지만 이 젯슨에 `rviz2`가 설치돼 있지 않다(ROS가
`ros-base`로 깔려 GUI 패키지가 빠져 있다). 판정 기준은 도구와 무관하므로
Foxglove의 같은 패널로 본다.

수치로 확인하려면 이쪽이 정확하다.

```bash
source scripts/ros_env.sh
ros2 topic echo /pose --once      # 위치와 covariance
```

`covariance`의 0번(x), 7번(y), 35번(yaw) 성분이 분산이다. 제곱근이 표준편차이며
실측에서 x·y 3~5cm, yaw 0.7~1.0°였다. **값이 표본마다 변하는지 보라** — 고정
상수를 내보내는 구현이면 그 값은 의미가 없다.

#### 레이아웃을 저장해 팀과 공유한다

설정은 다음 접속에 사라지고 다른 팀원이 열면 기본 상태다. **시연 때 누가 켜도
같은 화면이 나와야 한다.**

1. 우측 상단 레이아웃 드롭다운(기본 이름 **Default**) → 다른 이름으로 저장한다
   (예: `sentinel-slam`).
2. 팀원에게 주려면 같은 드롭다운에서 레이아웃을 **export** 해 JSON 파일로 저장하고
   전달한다. 받는 쪽은 **import** 한다.

레이아웃은 저장소가 아니라 Foxglove 계정에 저장되므로 `git pull`로는 전파되지
않는다. 파일로 주고받는 것이 유일한 방법이다.

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

#### 왜 demo_up.sh에 enable_viz를 박아 두지 않는가

위 「켠 상태는 인증이 없다」와 같은 이유다. `demo_up.sh`는 systemd가 부팅마다
부르는 진입점이므로(위 「부팅 자동 시작」), 박아 두면 **무인 상태로 8765가 상시
열린다.** 이 기기는 NAT 뒤가 아니고 임의 포트가 외부에서 닿는다(WHEP 8889가
그렇게 동작한다).

기본 capability가 읽기 전용이 아니라는 것이 핵심이다.

```text
[clientPublish, parameters, parametersSubscribe, services, connectionGraph, assets]
```

토픽 발행·서비스 호출·파라미터 변경이 되고 인증이 없다. 주행 코드가 붙기
시작하면 이 노출의 의미가 달라진다.

### 손으로 ros2 명령을 칠 때는 ros_env.sh를 source한다

```bash
source scripts/ros_env.sh
ros2 node list
```

`source /opt/ros/humble/setup.bash`만 하면 **DDS 격리 설정이 빠진다.** 그 상태로
`ros2 topic echo`를 하면 다른 팀 그래프의 토픽이 섞여 보이고, 반대로 스택 노드는
안 보인다. 자주 쓰면 `~/.bashrc`에 위 한 줄을 두는 편이 낫다(기기 설정이라
저장소에서 강제하지 않는다).

#### 왜 격리가 필요한가

이 설정이 비어 있던 동안 **다른 팀의 ROS 그래프가 이 젯슨과 섞여 있었다**
(S15P11A301-218). `ros2 node list`에 남의 nav2 스택 두 벌(`/sim_f02/*`,
`/sim_f03/*`)과 `/kinematic_fleet`, `/rviz`, `/map_server`가 있었다.

DDS는 IP가 아니라 **LAN 멀티캐스트로** 상대를 찾는다. 같은 망 + 같은
`ROS_DOMAIN_ID`면 서로를 찾으므로, 기본값 0에 여러 팀이 함께 있었다. 젯슨 IP를
바꿔도 해결되지 않는다.

실제 피해가 확인된 곳:

| 토픽 | 실측 | 결과 |
|---|---|---|
| `/map` | 발행자 2 | `map_saver`가 어느 지도를 저장할지 보장이 없었다. latched라 늦게 붙은 구독자가 남의 값을 받을 수 있다 |
| `/map` 구독자 | 남의 costmap 2개 | 우리 SLAM 지도가 남의 nav2로 흘러갔다 |
| `/tf` / `/tf_static` | 발행자 4 / 5 | TF 트리가 섞여 Foxglove 3D를 읽을 수 없었다 |
| `/cmd_vel` | 남의 `kinematic_fleet`이 구독 | 주행 코드를 붙이면 우리 명령이 남의 시뮬레이터로 간다 |

`ROS_LOCALHOST_ONLY=1`이 주 수단이다. DDS를 루프백에 묶어 LAN 디스커버리 자체를
없앤다 — 도메인 격리는 합의지만 이것은 물리적 차단이다. 우리는 ROS 통신이 전부
젯슨 안에서 끝나므로(밖으로 나가는 것은 MQTT·HTTPS·WHEP·WebSocket) 잃는 것이 없다.

`ROS_DOMAIN_ID`도 함께 둔다. `ROS_LOCALHOST_ONLY`가 Humble 이후 폐기 예정이라
배포를 올릴 때 조용히 무력화될 수 있다.

**노트북에서 `ros2` CLI로 젯슨 토픽을 보는 경로는 끊긴다.** Foxglove로 본다.

### 내릴 때 무엇이 남는가

`demo_up.sh`가 띄우는 것을 실측으로 열거했다. `demo.launch.py` 아래 15개다.

| 프로세스 | 메모리 | `stop_sentinel.sh` | `demo_down.sh` |
|---|---|---|---|
| `ros2 launch ... demo.launch.py` | 66MB | | O |
| `usb_cam_node_exe` | 68MB | O | O |
| `ydlidar_ros2_driver_node` | 25MB | O | O |
| `robot_state_publisher` | 26MB | O | O |
| `static_transform_publisher` | 26MB | | O |
| `async_slam_toolbox_node` | **551MB** | | O |
| `mediamtx` | 49MB | O | O |
| `stream_pipeline` | **456MB** | O | O |
| `recording_manager` | 46MB | | O |
| `map_saver` | 50MB | | O |
| `map_uploader` | 50MB | | O |
| `media_uploader` | 51MB | | O |
| `mission_manager` | 60MB | | O |
| `sentinel_voice.ros_node` | 42MB | | O |
| `src.ros_main` (탐지) | **1601MB** | | O |

`stop_sentinel.sh`로 내리면 **9개가 남고 그중 탐지 1601MB와 SLAM 551MB가 있다.**
8GB 장비에서 2.1GB가 물린 채로 다음 실행을 시도하게 된다.

이 사고가 두 번 났다. S15P11A301-192에서 "메모리 부족"의 원인을 VSCode로 잘못
짚었는데, 실제 원인은 teardown이 `src.ros_main`을 빼먹어 CUDA 컨텍스트가 계속
잡혀 있던 것이었다. 그 앞에는 `usb_cam`을 빼먹은 같은 사고가 있었다.

그래서 `demo_down.sh`는 **정리한 뒤 다시 훑어 확인하고 남아 있으면 실패로 끝낸다.**
"끄는 명령을 실행했다"와 "실제로 다 내려갔다"는 다르다.

```bash
./scripts/demo_down.sh --dry-run    # 패턴이 무엇을 잡는지 먼저 본다
./scripts/demo_down.sh
```

`--dry-run`이 있는 이유는 노드를 추가했을 때 목록에서 빠지는 것을 **스택을
내리지 않고** 확인해야 하기 때문이다. 새 노드를 만들면 `demo_down.sh`의
`node_patterns`에도 넣고 `--dry-run`으로 잡히는지 확인한다.

순서는 SIGINT → TERM → KILL이다. 곧바로 KILL 하지 않는 이유는 녹화가 파일을
쓰는 중일 수 있어서다. `ros2 launch`에 SIGINT를 보내면 자식들이 정상 종료한다.

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
| `ci/validate_node_callbacks.py` | ROS 노드가 구독·타이머·서비스에 넘긴 콜백이 그 클래스에 실제로 있는지 |
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

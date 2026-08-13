<div align="center">

![Sentinel UGV Header](https://capsule-render.vercel.app/api?type=waving&height=260&color=0:0C0C0F,45:1F4E79,100:E8873A&text=Sentinel%20UGV&fontColor=FFFFFF&fontSize=74&fontAlign=50&fontAlignY=38&desc=%EC%9E%AC%EB%82%9C%20%ED%98%84%EC%9E%A5%20%EC%9E%90%EC%9C%A8%20%ED%83%90%EC%82%AC%20%EB%A1%9C%EB%B4%87%20%C2%B7%20%EC%98%A8%EB%94%94%EB%B0%94%EC%9D%B4%EC%8A%A4%20AI%20%EA%B4%80%EC%A0%9C%20%EC%8B%9C%EC%8A%A4%ED%85%9C&descSize=18&descAlign=50&descAlignY=58&animation=fadeIn)

### 사람이 들어가기 전에, 로봇이 먼저 들어가 *묻습니다*

[![Pipeline](https://lab.ssafy.com/s15-webmobile3-sub1/S15P11A301/badges/develop/pipeline.svg)](https://lab.ssafy.com/s15-webmobile3-sub1/S15P11A301/-/pipelines)

![시연](https://img.shields.io/badge/%EC%8B%9C%EC%97%B0-%EC%A0%84_%EA%B5%AC%EA%B0%84_%EC%8B%A4%EA%B8%B0%EB%8F%99-2E7D32?style=flat-square)
![MVP](https://img.shields.io/badge/MVP-14%2F16%20%EA%B5%AC%ED%98%84-2E7D32?style=flat-square)
![자동 시험](https://img.shields.io/badge/%EC%9E%90%EB%8F%99%20%EC%8B%9C%ED%97%98-941%EA%B1%B4-2E7D32?style=flat-square)
![순항 속도](https://img.shields.io/badge/%EC%88%9C%ED%95%AD-0.30m%2Fs%20%28%EC%8B%A4%EC%86%8D%EB%8F%84%2099%25%29-455A64?style=flat-square)
![Detect](https://img.shields.io/badge/Detect-15%20FPS-455A64?style=flat-square)
![영상](https://img.shields.io/badge/%EC%98%81%EC%83%81-15FPS%20%C2%B7%201500kbps-455A64?style=flat-square)

**로봇 · 인식**

![ROS 2](https://img.shields.io/badge/ROS%202%20Humble-22314E?style=for-the-badge&logo=ros&logoColor=white)
![Jetson](https://img.shields.io/badge/Jetson%20Orin%20Nano-76B900?style=for-the-badge&logo=nvidia&logoColor=white)
![YOLO](https://img.shields.io/badge/YOLO26%20%C2%B7%20BoT--SORT-111F68?style=for-the-badge&logo=ultralytics&logoColor=white)
![Nav2](https://img.shields.io/badge/Nav2%20Smac%20%C2%B7%20SLAM%20Toolbox-22314E?style=for-the-badge&logo=ros&logoColor=white)
![ESP32](https://img.shields.io/badge/ESP32%20%C3%972-E7352C?style=for-the-badge&logo=espressif&logoColor=white)

**음성 상호작용**

![Qwen3-ASR](https://img.shields.io/badge/Qwen3--ASR%201.7B-6F42C1?style=for-the-badge&logo=alibabacloud&logoColor=white)
![Silero VAD](https://img.shields.io/badge/Silero%20VAD-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)
![GMS](https://img.shields.io/badge/GMS%20gpt--5.4--mini-8D73FF?style=for-the-badge)
![DeepFilterNet](https://img.shields.io/badge/DeepFilterNet-1B6AC6?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)

**관제 · 인프라**

![Next.js](https://img.shields.io/badge/Next.js%2014-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)
![Spring Boot](https://img.shields.io/badge/Spring%20Boot%204-6DB33F?style=for-the-badge&logo=springboot&logoColor=white)
![TimescaleDB](https://img.shields.io/badge/TimescaleDB-FDB515?style=for-the-badge&logo=postgresql&logoColor=white)
![Mosquitto](https://img.shields.io/badge/Mosquitto%20MQTT%205-3C5280?style=for-the-badge&logo=eclipsemosquitto&logoColor=white)
![MinIO](https://img.shields.io/badge/MinIO-C72E49?style=for-the-badge&logo=minio&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)

[할 수 있는 것](#할-수-있는-것) · [시연](#시연) · [실행](#로봇-스택-실행) · [모듈별 시작](#개발-시작) · [문서](#개발-문서) · [안전](#안전-원칙) · [상태](#프로젝트-상태) · [트러블슈팅](docs/TROUBLESHOOTING.md) · [TBD](docs/TBD.md)

</div>

# Sentinel UGV

재난·사고 현장을 자율 탐사하는 온디바이스 AIoT 기반 무인 지상 차량(UGV) 프로젝트입니다.
Jetson Orin Nano에서 ROS 2, SLAM, Nav2, 사람 탐지와 안전 제어를 수행하고, 발견한 요구조자와
음성으로 상태를 확인하며, Spring Boot·Next.js 기반 관제 시스템에서 실시간 상태와 임무 이력을
제공합니다.

시연 시나리오 **「탐사 시작 → 자율 탐사 → 사람 발견 → 접근 → 음성 대화 → 보고 → 임무 종료」**
전 구간이 실기동으로 동작합니다. 무엇을 하는 로봇인지는 [할 수 있는 것](#할-수-있는-것)에,
미구현·폐기·제한은 [프로젝트 상태](#프로젝트-상태)에 나눠 적었습니다. 전체 기준은 통합 명세서
[v2.1](docs/README.md)입니다.

## 시연

<!-- 시연 영상 — VIDEO_ID 두 곳을 YouTube 영상 ID로 바꾸고 이 주석을 푼다.
     GitLab 마크다운은 <iframe> 을 sanitize 하므로 플레이어는 심을 수 없고 썸네일
     링크가 유일한 방법이다. img.youtube.com 을 쓰면 레포에 파일을 추가하지 않아도
     되고, 영상을 다시 편집해 올려도 ID 가 같으면 포스터가 따라온다.
<div align="center">
  <a href="https://youtu.be/VIDEO_ID">
    <img src="https://img.youtube.com/vi/VIDEO_ID/maxresdefault.jpg" width="720" alt="Sentinel UGV 시연 영상">
  </a>
</div>
-->

<!-- 관제 화면 — 캡처를 frontend/docs/screen-gcs-main.webp 로 넣고 이 주석을 푼다.
     그 폴더에는 같은 화면의 와이어프레임 4장이 이미 있어 설계와 결과가 나란히 놓인다.
     넣기 전에 호스트명·IP 가 찍혔는지 확인한다 — API·MediaMTX·8765 가 모두 무인증이라
     화면에 보이는 주소가 그대로 접근 경로다(06장 36-4).
<div align="center">
  <img src="frontend/docs/screen-gcs-main.webp" width="860" alt="Sentinel UGV 관제 화면 — 실시간 영상·지도·상태">
</div>
-->

시연 영상과 관제 화면 캡처는 추가 예정입니다. 관제 화면 설계는
[frontend/docs/wireframe.md](frontend/docs/wireframe.md), 시연 시나리오 전문은
[01장 4.2 핵심 시연 시퀀스](docs/01-프로젝트-개요.md#42-핵심-시연-시퀀스)입니다.

## 할 수 있는 것

MVP 필수 기능([01장 2.2](docs/01-프로젝트-개요.md#22-mvp-필수-기능)) 16개 중 **14개를 구현했습니다.**
남은 둘은 **MVP-13 자동 복귀**(home pose 저장까지만 되어 있고 복귀 주행이 없다)와 **MVP-06의 사람
map 좌표 추정**(encounter 생성은 되지만 위치가 로봇 위치로 남는다 — 부분 구현이므로 셈에서 뺐다)이며,
사유는 [끝내지 못한 것](#끝내지-못한-것)에 적었습니다. 아래 수치는 전부 실기동 실측입니다.

**미지 공간을 스스로 돌아다닙니다**

- YDLIDAR X4 Pro와 SLAM Toolbox로 2D 지도를 실시간 생성하고, Frontier로 미탐사 영역을 스스로
  고릅니다. 지도는 임무마다 초기화됩니다(`MISSION_START`에서 SLAM 재시작).
- 경로는 Nav2 **Smac Hybrid-A\***(REEDS_SHEPP) + Regulated Pure Pursuit입니다. **제자리 회전을
  하지 못하는 전륜 조향 차량**이라는 제약에서 나온 선택이며, 실측 `R_min` 좌 1.37m·우 1.76m를
  안전측 1.8m로 planner에 넣었습니다.
- 순항 0.30m/s 명령 대비 실속도 99%, EKF yaw는 90° 회전에서 오차 1.1%입니다.

**사람을 찾아 안전거리까지 접근합니다**

- YOLO26n Detect로 person을 상시 약 15FPS 탐지하고 BoT-SORT로 추적합니다. 같은 사람을 시간·위치
  조건으로 하나의 encounter에 묶습니다.
- 3프레임 이상 연속 감지되면 Pose를 약 2FPS로 **조건부** 실행해 쓰러짐을 판정합니다. 임계값은
  E-FPDS 정답 2,658건과 대조해 검증했습니다(쓰러짐 점수 중앙 0.919 / 비쓰러짐 0.032).
- bearing-only 주행으로 1.5~2.0m 안전거리까지 접근합니다(접근 속도 0.25m/s).

**발견한 사람에게 말을 걸고 답을 정리합니다**

- 마이크 → Silero VAD → 원격 Qwen3-ASR-1.7B(L40S FastAPI) → GMS 구조화 → 승인된 사전 녹음 안내
  재생으로 이어집니다.
- **STT 실패를 요구조자의 무응답으로 분류하지 않습니다.** 위험도는 LLM이 아니라 규칙이 산출합니다.

**관제에서 실시간으로 보고 명령합니다**

- WebRTC 저지연 영상(15FPS·1500kbps), Foxglove 브리지로 받는 실시간 SLAM 지도, 2Hz 텔레메트리.
- 임무 시작·일시정지·재개·종료, 자율/수동 모드 전환, 모바일 페이지 수동 조종.
- 사람 확정 전 3초 + 상호작용 전체 + 종료 후 3초를 이벤트 영상으로 잘라 S3에 올립니다.
- 임무·시계열·이벤트·미디어를 TimescaleDB와 S3에 남기고 과거 임무 페이지에서 조회합니다.

**안전은 여러 층으로 막습니다**

- ESP32 watchdog 300ms, 수동 조종 TTL 250ms, `collision_monitor` 정지·감속 구역(실측 차체 기준),
  `safety_gate`, 관제 임무 정지. 물리 E-Stop 미도입이 이 프로토타입의 가장 큰 안전 한계이며
  [안전 원칙](#안전-원칙)에 그대로 적었습니다.
- 자동 시험 **941건**이 CI에서 돕니다 — ROS 2 682 · 프런트 171 · 백엔드 55 · 탐지 33.

## 시스템 구성

- **차량**: BMW M7 유아전동차 베이스. 후륜 좌·우 RS540 2개(BTS7960 2개)가 전·후진, 전륜 타이로드에
  직결된 DS51150 서보가 조향을 담당합니다. 조향 기하가 있어 **제자리 회전은 하지 못합니다**
  (휠베이스 0.683m, 바퀴 조향각 최대 22°).
- **Jetson**: 센서 수집, SLAM(SLAM Toolbox), 자율 탐사(Frontier), Nav2 주행, EKF 오도메트리,
  안전 체인, 이벤트 녹화·스트리밍을 하나의 launch로 실행합니다.
- **ESP32 2개**: 모터 보드가 구동 PWM·조향 서보·300ms watchdog을, 센서 보드가 엔코더 2개·IMU·
  온습도·초음파 2개를 담당합니다. 각각 독립된 USB Serial로 Jetson에 붙습니다.
- **AI**: YOLO26n Detect·BoT-SORT 사람 탐지·추적과 조건부 Pose 쓰러짐 판정(`ai/detection`),
  음성 상호작용·원격 ASR 서버·녹음 후처리 잡음 제거(`ai/voice`).
- **Backend**: 임무·텔레메트리·이벤트 API, MQTT 구독, STOMP/WebSocket, S3 호환 스토리지 연계.
- **Frontend**: 실시간 영상·지도·상태 관제, 운행 모드 전환과 임무 명령, 임무 이력.
- **Infrastructure**: PostgreSQL/TimescaleDB, MinIO, Mosquitto, MediaMTX, Docker Compose.
- **Common**: 외부 프로토콜, 스키마, 샘플 메시지의 단일 기준점.

## 저장소 구조

```text
.
├─ jetson/                  # 로봇 온보드 소프트웨어
│  ├─ ros2_ws/src/          # ROS 2 패키지 13개
│  │  ├─ sentinel_{bringup,drive,exploration,safety,bridge}/
│  │  ├─ sentinel_{approach,mission,recorder,streaming,description}/
│  │  └─ esp32_bridge/, usb_cam/, ydlidar_ros2_driver/
│  ├─ models/               # 모델 메타데이터(가중치·엔진은 Git 제외)
│  └─ streaming_poc/        # 스트리밍 PoC 기록
├─ ai/
│  ├─ detection/            # YOLO 사람 탐지·추적 (ROS 노드가 아니라 .venv 파이썬)
│  └─ voice/                # 음성 파이프라인·ASR 서버·평가 도구·잡음 제거 워커
├─ hardware/
│  ├─ esp32/{motor,sensor,jetson-comm}/   # 펌웨어(Arduino-ESP32)와 프레이밍·CRC 시험 벡터
│  └─ cad/, wiring/, bom/   # 기구·배선·BOM 산출물
├─ backend/                 # Spring Boot 관제 API (Dockerfile·compose 포함)
├─ frontend/                # Next.js 관제 웹
├─ common/                  # 공유 프로토콜·스키마·샘플
├─ scripts/                 # 스택 진입점(demo_up.sh/demo_down.sh)·설치·계측 스크립트
├─ docs/                    # 통합 명세서(01~08장)·TBD 대장·트러블슈팅·Git 컨벤션
└─ .gitlab-ci.yml           # GitLab CI/CD 파이프라인
```

## 로봇 스택 실행

Jetson에서 스택을 올리고 내리는 진입점은 **둘뿐**입니다. 다른 경로로 띄운 스택은 중복 기동
검사와 정리 대상에서 빠질 수 있습니다.

```bash
./scripts/demo_up.sh                       # 기본 구성으로 기동
./scripts/demo_up.sh enable_esp32:=true enable_ekf:=true \
  enable_nav2:=true enable_exploration:=true enable_approach:=true enable_safety:=true
./scripts/demo_down.sh                     # 내리기(systemd 유닛도 함께 본다)
```

- 기본값은 SLAM·스트리밍·녹화·임무·클라우드 브리지·음성·탐지·시각화가 `true`,
  **ESP32·Nav2·탐사·접근·안전·EKF가 `false`** 입니다. 실주행에 필요한 기능은 인자로 켭니다.
- **`enable_ekf`를 빠뜨리면 스택이 조용히 침묵합니다**(S15P11A301-359). EKF를 끈 구성의 yaw는
  근거가 없으므로 주행 판단에 쓰지 않습니다.
- `sentinel-demo.service`가 active인 동안 손으로 `demo_up.sh`를 부르면 거부됩니다. 두 벌이 뜨면
  증상이 「안 뜬다」가 아니라 영상 간헐 끊김·시리얼 경합으로 나타나 원인 추적이 어렵습니다.
- 증상별 진단 경로는 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), 스크립트 상세는
  [scripts/README.md](scripts/README.md)입니다.

## 개발 시작

모듈마다 실행 환경이 다르므로 아래 README에서 시작합니다.

| 대상 | 실행·검증 문서 |
|---|---|
| 관제 API | [backend/README.md](backend/README.md) |
| 관제 웹 | [frontend/README.md](frontend/README.md) |
| Jetson 전체 스택 | [jetson/README.md](jetson/README.md) |
| ESP32 펌웨어·배선 | [hardware/README.md](hardware/README.md) |
| 사람 탐지 | [ai/detection/README.md](ai/detection/README.md) |
| 음성 상호작용 | [ai/voice/README.md](ai/voice/README.md) |
| 공통 메시지 계약 | [common/README.md](common/README.md) |

환경변수는 루트 [`.env.example`](.env.example)을 참고하되, 실제 로딩 위치에 맞게
`backend/.env.local` 또는 `frontend/.env.local`로 나눠 둡니다. Jetson 런타임 값은 환경변수가
아니라 ROS 파라미터 YAML과 `~/.config/sentinel/secrets.yaml`에서 관리합니다.

작업 전에는 Jira 이슈를 만들고 최신 `develop`에서 `<type>/<scope>/<jira-key>-<description>` 형식의
브랜치를 생성합니다. 공통 메시지를 바꾸면 `common/`의 스키마·샘플과 생산자/소비자 테스트를 함께
갱신하고, 코드가 `docs/`의 서술과 어긋나게 되면 같은 MR에서 문서를 고칩니다.

## 개발 문서

통합 명세서는 장 번호(1~38장)와 부록(A~L)이 전역으로 이어지는 하나의 문서이며, 역할별 파일로
나눠 관리합니다. 문서 버전·변경 이력은 [docs/README.md](docs/README.md)에서만 관리합니다.

| 문서 | 담는 것 |
|---|---|
| [docs/README.md](docs/README.md) | 문서 규칙·버전·변경 이력·읽기 경로·용어집 |
| [01-프로젝트-개요](docs/01-프로젝트-개요.md) | 개요·목표·시나리오·아키텍처·기술 스택·CI/CD·일정·완료 기준 |
| [02-하드웨어](docs/02-하드웨어.md) | 기구·인터페이스·전원·배선, BOM·핀맵 |
| [03-제어-캘리브레이션](docs/03-제어-캘리브레이션.md) | ESP32 저수준 제어·안전 통신, 센서·엔코더·조향 보정 |
| [04-자율주행](docs/04-자율주행.md) | ROS 그래프·상태 머신·TF·SLAM·Nav2·Mission Manager |
| [05-통신-서버-영상](docs/05-통신-서버-영상.md) | 통신 계약(규범)·Spring Boot·Next.js·DB/S3·스트리밍·녹화 |
| [06-테스트-보안-운영](docs/06-테스트-보안-운영.md) | 테스트 계획·보안·운영·요구사항 추적·인수 시험·파라미터 동결표 |
| [07-AI-탐지](docs/07-AI-탐지.md) | 사람 탐지·추적·자세 판정·피해자 오케스트레이션 |
| [08-AI-음성](docs/08-AI-음성.md) | 음성 상호작용·GPU ASR·Jetson 실행·실측 |
| [TBD.md](docs/TBD.md) | 미확정·잔여 항목 단일 대장(담당·기한) |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 증상에서 규범 절차로 가는 현장 진단 인덱스 |
| [git_convention.md](docs/git_convention.md) | 브랜치·커밋·MR 규칙 |

## 라이선스

**프로젝트 자체 라이선스는 아직 정하지 않았습니다.** 루트에 `LICENSE` 파일이 없습니다.

제3자 구성요소와 그 라이선스는 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)에 있습니다.
확인한 것과 아직 확인하지 않은 것을 나눠 적었습니다. **`ultralytics`가 AGPL-3.0이라 저장소를
공개·배포할 때 선택지가 제약됩니다** — AGPL은 네트워크 너머로 서비스를 제공하는 경우까지 소스
공개 의무를 걸고, 이 프로젝트는 관제 웹이 탐지 결과를 제공하므로 그 경계에 닿습니다. 결정 전에
그 문서를 먼저 읽습니다.

프런트엔드 글꼴·컴포넌트 고지는 [frontend/ATTRIBUTIONS.md](frontend/ATTRIBUTIONS.md)입니다
(Asta Sans·D2Coding은 SIL OFL 1.1로 **고지 의무**가 있습니다).

## 안전 원칙

- **래칭형 물리 E-Stop 스위치는 도입하지 않았습니다.** 하드웨어 차단 수단은 12V 모터 배터리
  연결 분리뿐이며, 시연·시험에서는 이를 담당할 사람을 지정합니다(03장 34-10).
- 실제 정지 수단은 ESP32 watchdog 300ms, 수동 조종 TTL 250ms, `collision_monitor` 정지 구역,
  `safety_gate`, 관제 임무 정지입니다. 전·후방 초음파는 **거리 관측 전용**이며 보호 정지 발동은
  꺼져 있습니다.
- 예상하지 못한 주행·조향이 있으면 관제에서 임무를 정지하고 차량을 들어 바퀴를 띄웁니다.
  장애 뒤 모터를 자동 재개하지 않고, 원인을 확인한 뒤 `SAFE_IDLE`에서 명시적으로 시작합니다.
- 조향은 개루프이고 서보가 각도·fault를 출력하지 않아 **링키지 이탈·서보 고장을 전기적으로
  감지할 수 없습니다.** 기동 전 앞바퀴를 띄운 상태로 조향 스윕을 점검합니다.
- 모터·전원·안전 체인 변경은 실제 장치 검증과 임베디드 담당 리뷰가 필요합니다.
- 프로그램 시작·종료·예외 및 제어 명령 TTL 초과 시 모터는 정지 상태여야 합니다.
- `.env`, 인증서, SSH 키, 모델 가중치와 클라우드 자격 증명은 커밋하지 않습니다.
- 개인 음성(요구조자·팀원 녹음)은 어떤 경우에도 커밋하지 않습니다.

## 프로젝트 상태

명세 v2.1(2026-08-11, 시연 종료 시점 코드·실측 정합본) 기준입니다.

### 실기동으로 확인된 것

기능 목록은 [할 수 있는 것](#할-수-있는-것)에 있습니다. 여기에는 **그 구현이 실제로 어느 층에서
성립하는지** — 설계 문서만 보면 오해하기 쉬운 것들을 적습니다.

- 센서·주행 명령 체인, 임무 상태 머신, 자율 탐사, 사람 탐지·접근, 음성 상호작용, 스트리밍·
  이벤트 녹화, MQTT·REST·STOMP 관제 경로가 전 구간 실기동으로 확인됐습니다.
- 주행은 Nav2 **Smac Hybrid-A\*** (REEDS_SHEPP, `minimum_turning_radius` 1.8) + Regulated Pure
  Pursuit(후진 추종) 구성이지만, **곡률 상한을 실제로 지키는 층은 planner 도 controller 도 아니라**
  `vehicle_kinematics`의 δ 클램프입니다(RPP에는 곡률 상한 기능 자체가 없습니다 — 04장 24.1).
- 지도는 임무마다 초기화됩니다(`MISSION_START`에서 SLAM 재시작). 재개(`RESUME`)에는 걸리지 않습니다.
- 안전의 주 방어는 실측 차체 기준으로 다시 그린 `collision_monitor` 정지·감속 구역입니다.
  초음파는 관측 전용이라 이 방어에 들어가지 않습니다.

### 확정된 실측값

| 항목 | 값 |
|---|---|
| 최소 회전반경 `R_min` | 좌 1.37m · 우 1.76m (Smac에는 안전측 1.8m) |
| 순항 속도 | 0.30m/s 명령 대비 실속도 99% |
| 접근 속도 | 0.25m/s |
| 펌웨어 데드밴드 | 0.15m/s — 이 아래 명령은 실속도 0 |
| 조향 링키지 | 서보 ±55° / 바퀴 ±22° (2.5:1) |
| EKF yaw | 90° 회전에서 89.02°(오차 1.1%), 정지 12초 드리프트 0.14° |
| 오도메트리 | 단거리 줄자 대비 3% 이내 |
| 카메라·영상 | 캡처 1280×720 MJPEG 29.93FPS, 관제 인코딩 15FPS·1500kbps, 링 버퍼 약 1.6MB |
| 탐지 | Detect 상시 약 15FPS 달성, Pose는 조건부 약 2FPS |

### 기본 기동에서 꺼져 있는 것

`enable_esp32`, `enable_nav2`, `enable_exploration`, `enable_approach`, `enable_safety`,
`enable_ekf`는 기본 `false`입니다. 특히 `enable_safety:=true`는 실제 모터 명령 경로를 연결합니다.
반대로 SLAM·스트리밍·녹화·임무·클라우드 브리지·음성·탐지·시각화는 기본 `true`입니다.

### 범위에서 뺀 것 — 결정과 근거

- **애플리케이션 인증**을 MVP 범위 밖으로 확정했습니다(36-4). `/api/**`가 `permitAll()`이고
  로그인 화면과 `users` 테이블이 없습니다. 배포 경계는 네트워크 허용 범위·CORS·TLS이며,
  Control Session은 조종권 중재이지 인증이 아닙니다. MediaMTX 스트림 경로와 Jetson 8765
  Foxglove 읽기 경로도 같은 상태입니다(읽기 전용·토픽 화이트리스트·TLS로 범위만 좁혔습니다).
- **배터리 기반 안전·종료를 폐기**했습니다(14.6). 전압·전류 센서를 장착하지 않아 계측 경로 자체가
  없습니다. 있는 것처럼 남겨 두면 없는 보호를 믿게 되므로 표시·판정·종료 조건에서 모두 뺐고,
  충전·전압은 시연 전에 사람이 확인합니다.
- **YOLO 파인튜닝을 미채택**했습니다. 3개 데이터셋 × 4가지 방법을 교차 평가했으나 일반화 성능이
  하락해 COCO 사전학습 가중치를 그대로 씁니다(25.4).
- **게임패드 조종을 폐기**하고 모바일 페이지로 대체했습니다(28장). 조종 입력은 폰이 모터 ESP32에
  직결하는 경로가 담당합니다.
- **Frontier 소진 자동 종료를 넣지 않기로** 했습니다(23.4). 지도 완성을 수색 완료로 쓸 수 없다는
  판단입니다 — 라이다는 360°·원거리라 방 중앙을 한 번 지나가면 지도가 완성되는데, 사람을 찾는
  것은 전방 약 52° 카메라입니다. frontier만 좇으면 지도는 완벽한데 구석에 쓰러진 사람은 화각에
  한 번도 들어오지 않습니다. 종료 판정을 넣는다면 기준은 frontier 소진이 아니라 커버리지
  충족이어야 하고, 그건 실차 검증이 필요한 크기입니다.
- **통계 집계·retention**(Continuous Aggregate, TimescaleDB retention)은 선택 기능이라 원본
  시계열 조회로 갈음했습니다.

### 끝내지 못한 것

- **자동 복귀(`RETURNING`)** — home pose 저장까지만 되어 있고 복귀 주행이 없습니다. 임무는
  `COMPLETED`로 끝나고 로봇은 종료 지점에 섭니다(38-3 FR-020).
- **7분 탐사 타임아웃** — `mission_state.tick()`이 탐사 경과를 재지 않고 관련 파라미터도 없습니다.
  자동 종료 조건이 없어 임무는 운영자 STOP으로만 끝납니다.
- **사람의 map 좌표(`human_localizer`)** — encounter pose는 확정 시점의 로봇 위치이며, 카메라
  방위각·LiDAR 거리 결합은 접근 제어에만 씁니다.
- **카메라 hfov 실측** — 접근 방향 미세 오차의 유력 원인입니다(S15P11A301-371).
- 브라우저 bbox 오버레이와 `detections` 적재, 로컬/원격 스트림 자동 전환(현재 수동 토글).

### 알려진 제한

- 전·후방 초음파는 장착·발행까지 구현했으나 **거리 관측 전용**입니다. 전방은 빈 공간 오측 때문에
  발동을 껐고(커넥터 분리 유지) 후방은 정지 판정에 넣지 않았습니다. 1.4m에서 탐지율이 7~27%라
  임계를 올리면 놓치는 비율이 그대로 오판이 되므로, 넓히지 않고 좁은 범위를 정확히 아는 쪽을
  택했습니다(22.2 실측).
- 구동 속도·조향은 엔코더 폐루프 PID가 아니라 실측 회귀 기반 **개루프**입니다. 조향은 서보가
  각도·fault를 출력하지 않아 링키지 이탈을 전기적으로 감지할 수 없고, IMU 기대 yaw rate 대조가
  유일한 간접 판정입니다.
- 프런트엔드 상수 `USE_MOCK`는 이름보다 범위가 훨씬 좁습니다
  ([RobotContext.tsx](frontend/features/robot/RobotContext.tsx#L31)). 임무 명령·조회, 텔레메트리,
  온습도·MCU 상태, 지도, STOMP 푸시는 모두 실 경로입니다. **남은 목은 접속 연출(1초 뒤
  `connected`) 하나뿐**입니다 — `lidarOk`·`cameraOk` 램프 고정과 미사용 `sendControl`의 no-op은
  S15P11A301-377에서 걷어냈습니다.

> **왜 미구현을 적는가.** 명세가 "구현한다"고 적고 있는데 실제로 없으면, 통합 검사에서 "왜 안
> 되나"를 매번 다시 조사하게 되고 최악의 경우 **보안처럼 없는 보호를 있다고 믿습니다.** 그래서
> 이 저장소는 폐기한 것은 폐기로, 안 하기로 한 것은 안 한다고 적습니다(명세 v2.0 원칙).

구현 상태의 세부 근거와 남은 검증은 [통합 명세서](docs/README.md)와
[TBD 대장](docs/TBD.md)을 기준으로 합니다.

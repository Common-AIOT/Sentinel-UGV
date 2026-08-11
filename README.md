<div align="center">

![Sentinel UGV Header](https://capsule-render.vercel.app/api?type=waving&height=260&color=0:0C0C0F,45:1F4E79,100:E8873A&text=Sentinel%20UGV&fontColor=FFFFFF&fontSize=74&fontAlign=50&fontAlignY=38&desc=%EC%9E%AC%EB%82%9C%20%ED%98%84%EC%9E%A5%20%EC%9E%90%EC%9C%A8%20%ED%83%90%EC%82%AC%20%EB%A1%9C%EB%B4%87%20%C2%B7%20%EC%98%A8%EB%94%94%EB%B0%94%EC%9D%B4%EC%8A%A4%20AI%20%EA%B4%80%EC%A0%9C%20%EC%8B%9C%EC%8A%A4%ED%85%9C&descSize=18&descAlign=50&descAlignY=58&animation=fadeIn)

### 사람이 들어가기 전에, 로봇이 먼저 들어가 *묻습니다*

[![Pipeline](https://lab.ssafy.com/s15-webmobile3-sub1/S15P11A301/badges/develop/pipeline.svg)](https://lab.ssafy.com/s15-webmobile3-sub1/S15P11A301/-/pipelines)

**로봇 · 인식**

![ROS 2](https://img.shields.io/badge/ROS%202%20Humble-22314E?style=for-the-badge&logo=ros&logoColor=white)
![Jetson](https://img.shields.io/badge/Jetson%20Orin%20Nano-76B900?style=for-the-badge&logo=nvidia&logoColor=white)
![YOLO](https://img.shields.io/badge/YOLO26-111F68?style=for-the-badge&logo=ultralytics&logoColor=white)
![Nav2](https://img.shields.io/badge/Nav2%20%C2%B7%20SLAM%20Toolbox-22314E?style=for-the-badge&logo=ros&logoColor=white)
![ESP32](https://img.shields.io/badge/ESP32-E7352C?style=for-the-badge&logo=espressif&logoColor=white)

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

</div>

# Sentinel UGV

재난·사고 현장을 자율 탐사하는 온디바이스 AIoT 기반 무인 지상 차량(UGV) 프로젝트입니다. Jetson Orin Nano에서 ROS 2, SLAM, Nav2, 사람 탐지와 안전 제어를 수행하고, 발견한 요구조자와 음성으로 상태를 확인하며, Spring Boot·Next.js 기반 관제 시스템에서 실시간 상태와 임무 이력을 제공합니다.

## 시스템 구성

- **Jetson**: 센서 수집, SLAM, 자율 탐사, 주행 및 안전 제어, 이벤트 녹화·스트리밍
- **AI**: YOLO 사람 탐지·추적(`ai/detection`), 음성 상호작용·원격 ASR·잡음 제거(`ai/voice`)
- **Backend**: 임무·텔레메트리·이벤트 API, MQTT 구독, WebSocket, S3 호환 스토리지 연계
- **Frontend**: 실시간 영상·지도·상태 관제, 운행 모드 전환과 임무 명령, 임무 이력
- **Infrastructure**: PostgreSQL/TimescaleDB, MinIO, Mosquitto, MediaMTX, Docker Compose
- **Common**: 외부 프로토콜, 스키마, 샘플 메시지의 단일 기준점

## 저장소 구조

```text
.
├─ jetson/                  # 로봇 온보드 소프트웨어
│  ├─ ros2_ws/src/          # Sentinel ROS 2 패키지 (임무·탐사·안전·녹화·스트리밍·브리지)
│  ├─ config/               # 로봇 공통 설정
│  ├─ models/               # 모델 메타데이터(가중치는 Git 제외)
│  ├─ streaming_poc/        # 스트리밍 PoC 기록
│  └─ tests/                # 온보드 단위·통합 테스트
├─ ai/
│  ├─ detection/            # YOLO 사람 탐지·추적 (ROS 래퍼는 jetson/ros2_ws)
│  └─ voice/                # 음성 상호작용 파이프라인·ASR 서버·평가 도구·잡음 제거 워커
├─ backend/                 # Spring Boot 관제 API (Dockerfile·compose 포함)
├─ frontend/                # Next.js 관제 웹
├─ common/                  # 공유 프로토콜·스키마·샘플
├─ scripts/                 # 설치·배포·점검 스크립트 (demo_up.sh / demo_down.sh)
├─ hardware/                # CAD, 배선, BOM 산출물
├─ docs/                    # 통합 명세서(01~08장)·Git 컨벤션
└─ .gitlab-ci.yml           # GitLab CI/CD 파이프라인
```

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
`backend/.env.local` 또는 `frontend/.env.local`로 나눠 둡니다. Jetson 런타임 값은
환경변수가 아니라 ROS 파라미터 YAML과 `~/.config/sentinel/secrets.yaml`에서 관리합니다.

작업 전에는 Jira 이슈를 만들고 최신 `develop`에서
`<type>/<scope>/<jira-key>-<description>` 형식의 브랜치를 생성합니다. 공통 메시지를
바꾸면 `common/`의 스키마·샘플과 생산자/소비자 테스트를 함께 갱신합니다.

전체 개발 기준은 [통합 명세서](docs/README.md)와 [Git 컨벤션](docs/git_convention.md)을 참고하세요.

## 개발 문서

- [통합 명세서 (docs/README.md)](docs/README.md) — 장 번호가 전역으로 이어지는 단일 문서를 01~08장 파일로 분리해 관리합니다
- [AI 음성 설계·실험 기록 (docs/08-AI-음성.md)](docs/08-AI-음성.md)
- [Git 컨벤션](docs/git_convention.md)

## 안전 원칙

- 모터·E-Stop·전원 변경은 실제 장치 검증과 임베디드 담당 리뷰가 필요합니다.
- 프로그램 시작·종료·예외 및 제어 명령 TTL 초과 시 모터는 정지 상태여야 합니다.
- `.env`, 인증서, SSH 키, 모델 가중치와 클라우드 자격 증명은 커밋하지 않습니다.
- Jetson 배포 전 차량을 바닥에서 띄우고 물리 E-Stop 동작을 확인합니다.
- 개인 음성(요구조자·팀원 녹음)은 어떤 경우에도 커밋하지 않습니다.

## 프로젝트 상태

2026-08-11 코드 기준으로 센서·주행 명령 체인, 임무 상태 머신, 사람 탐지·접근,
음성 상호작용, 스트리밍·이벤트 녹화, MQTT·REST·STOMP 관제 경로가 구현되어 있습니다.
다만 안전상 다음 항목은 기본 기동에서 명시적으로 꺼져 있거나 범위가 제한됩니다.

- `enable_nav2`, `enable_exploration`, `enable_approach`, `enable_safety`, `enable_ekf`는 기본 `false`입니다. 특히 `enable_safety:=true`는 실제 모터 명령 경로를 연결합니다.
- 반대로 SLAM·스트리밍·녹화·임무·클라우드 브리지·음성·탐지·시각화는 기본 `true`입니다. 모터 ESP32 브리지는 기본 `false`입니다.
- 프런트엔드의 `USE_MOCK`는 현재 `true`인 코드 상수입니다. 실제 관제 연동 배포 전 반드시 확인합니다.
- Spring API의 `/api/**`와 MediaMTX 스트림 경로에는 애플리케이션 인증이 없습니다. Control Session은 조종권 중재이지 인증이 아니므로 네트워크 허용 범위·CORS·TLS를 배포 경계로 취급합니다.
- 자동 복귀(`RETURNING`)는 미구현입니다. 임무 종료 후 로봇은 종료 지점에서 정지합니다.
- 전·후방 초음파 거리는 `/range/front`·`/range/rear`로 발행합니다. 전방 `protective_stop`은 빈 공간 오측 때문에 펌웨어에서 발동을 껐고, 후방은 정지 판정과 Nav2 후진 안전 체인에 연결하지 않았습니다.
- 서보-바퀴 조향비 `55°/22°`, 엔코더 샘플 주기, mm/s→PWM 매핑 보정은 반영됐습니다. 실측 최소 회전반경은 좌 1.37m·우 1.76m이고 Smac 설정은 1.8m이며, 순항 0.30m/s 명령 대비 실속도는 99%입니다.

구현 상태의 세부 근거와 남은 검증은 [통합 명세서](docs/README.md)와
[TBD 대장](docs/TBD.md)을 기준으로 합니다.

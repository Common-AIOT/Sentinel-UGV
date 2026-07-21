# Local Development Setup

> 기준: 통합 프로젝트 명세서 v0.9, 7·15장 및 부록 D~F
> 상태: 공통 호스트 도구와 연결 규칙 **확정**, 프레임워크 세부 버전은 앱 생성 시 확정

## 목표

모든 개발자는 실제 UGV 없이도 담당 모듈을 실행하고 샘플 데이터로 검증할 수 있어야 합니다. 실제 센서와 모터가 필요한 검증은 로컬 개발과 분리해 예약된 벤치 테스트에서 수행합니다.

## 지원 개발 호스트

| 호스트 | 주 용도 | 필수 도구 |
|---|---|---|
| Windows 11 | Backend, Frontend, 공통 계약, Docker 인프라 | Git, Docker Desktop, PowerShell 7 권장 |
| Linux/WSL2 | Backend, Frontend, 스크립트, 일부 ROS 도구 | Git, Docker Engine, Compose plugin |
| Jetson Ubuntu 22.04 계열 | ROS 2, AI, 센서, 스트리밍, 하드웨어 통합 | JetPack 6.2 잠정, ROS 2 Humble, colcon, rosdep |

GitLab Runner는 팀 공용 실행 인프라이며 모든 개발자 PC에 설치할 필요가 없습니다.

## 버전 고정 원칙

- Jetson은 제공 이미지와 장치 드라이버를 확인한 뒤 JetPack 6.2와 ROS 2 Humble 적용 여부를 확정합니다.
- Backend Java/Gradle 버전은 Spring Boot 프로젝트 생성 시 Gradle wrapper로 고정합니다.
- Frontend Node/package manager 버전은 Next.js 프로젝트 생성 시 버전 파일과 lockfile로 고정합니다.
- Python 의존성은 Jetson 패키지별 requirements 또는 ROS package metadata로 고정합니다.
- Docker 이미지는 `latest` 대신 명시적 버전 또는 digest를 사용합니다.

현재 저장소에 wrapper나 lockfile이 없는 도구 버전을 문서만으로 임의 확정하지 않습니다.

## 최초 설정

```bash
git clone <repository-url>
cd S15P11A301
cp .env.example .env
```

Windows PowerShell에서는 다음을 사용합니다.

```powershell
git clone <repository-url>
Set-Location S15P11A301
Copy-Item .env.example .env
```

`.env`의 `change-me` 값은 로컬 전용 비밀번호로 변경할 수 있지만 저장소에 커밋하지 않습니다. AWS 키, Runner token, SSH 키와 실제 운영 URL도 커밋하지 않습니다.

## 공통 환경 점검

Windows:

```powershell
.\scripts\check_dev_environment.ps1
```

Linux, macOS 또는 Git Bash:

```bash
./scripts/check_dev_environment.sh
```

점검 스크립트는 Git, Docker Engine, Docker Compose와 Compose 설정을 필수로 확인합니다. Java, Node, Python, colcon, ROS 2는 해당 모듈이 생성되기 전까지 정보성으로 표시합니다.

## 환경 프로필

### 로컬 개발

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080/api
NEXT_PUBLIC_WS_URL=ws://localhost:8080/ws
NEXT_PUBLIC_LOCAL_STREAM_URL=http://sentinel.local:8889/robot/whep
NEXT_PUBLIC_REMOTE_STREAM_URL=http://localhost:8889/robot/whep
DB_URL=jdbc:postgresql://localhost:5432/sentinel
CONTROL_WS_URL=ws://localhost:8080/robot/ws
```

### 현장 로컬망

- Browser와 Jetson이 같은 Wi-Fi에 연결됩니다.
- API는 현장 Backend 주소, LOCAL stream은 Jetson/MediaMTX 주소를 사용합니다.
- IP를 소스 코드에 하드코딩하지 않고 `.env` 또는 배포 설정으로 주입합니다.

### EC2 원격

- 외부 endpoint는 HTTPS/WSS를 사용합니다.
- Nginx만 외부에 노출하고 DB와 Backend 내부 포트는 직접 공개하지 않습니다.
- 인증서, DB 비밀번호, AWS 설정은 EC2 `.env` 또는 GitLab CI 변수로 관리합니다.

## 환경 변수 책임

| 변수 | 소비자 | 의미 | 비밀 여부 |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Frontend/Browser | REST base URL | 공개 가능 |
| `NEXT_PUBLIC_WS_URL` | Frontend/Browser | 관제 WebSocket URL | 공개 가능 |
| `NEXT_PUBLIC_*STREAM_URL` | Frontend/Browser | LOCAL/REMOTE WHEP endpoint | 공개 가능, 운영 주소 주의 |
| `DB_URL`, `DB_USER`, `DB_PASSWORD` | Backend/Compose | PostgreSQL 연결 | password는 비밀 |
| `S3_BUCKET`, `AWS_REGION` | Backend | 미디어 저장 위치 | bucket은 환경 정보, AWS 키는 별도 비밀 |
| `CONTROL_LEASE_TTL_MS` | Backend | 서버 제어권 TTL | 공개 설정 |
| `ROBOT_ID` | Jetson | 로봇 식별자 | 운영 식별 정보 |
| `CONTROL_WS_URL` | Jetson bridge | 로봇용 WSS endpoint | 공개 가능, token은 별도 비밀 |
| `EVENT_PRE_SECONDS`, `EVENT_POST_SECONDS` | Jetson recorder | 이벤트 영상 버퍼 | 공개 설정 |
| `MANUAL_CMD_TTL_MS` | Jetson safety | 수동 명령 TTL | 안전 설정, 변경 리뷰 필요 |

브라우저에 전달되는 `NEXT_PUBLIC_` 변수에는 비밀번호, token, AWS credential을 절대 넣지 않습니다.

## 포트와 노출

| 포트 | 프로토콜 | 서비스 | 개발/배포 노출 |
|---:|---|---|---|
| 3000 | HTTP | Next.js | Compose 내부, 개발 서버에서 직접 접근 가능 |
| 8080 | HTTP/WS | Spring Boot | Compose 내부, 로컬 개발 시 직접 접근 가능 |
| 5432 | PostgreSQL | TimescaleDB | Compose 내부 전용, 필요 시 로컬 profile에서만 제한 노출 |
| 80/443 | HTTP/HTTPS | Nginx | 외부 진입점 |
| 8889 | HTTP/WebRTC | MediaMTX WHEP | 현장 또는 EC2 정책에 따라 노출 |
| 8189/UDP | ICE/UDP | MediaMTX | WebRTC 연결용 |

실제 포트 변경은 `.env.example`, Compose, Nginx, MediaMTX와 이 표를 같은 Merge Request에서 갱신합니다.

## Docker Compose 사용

Host에서 Backend와 Frontend를 개발할 때 PostgreSQL과 MediaMTX를 시작합니다.

```bash
docker compose --env-file .env -f deploy/local/docker-compose.yml up -d
docker compose --env-file .env -f deploy/local/docker-compose.yml ps
```

이 프로필만 PostgreSQL을 `localhost:5432`에 노출합니다. 상세 내용은 [`deploy/local/README.md`](../../deploy/local/README.md)를 참고합니다.

배포 Compose의 설정 렌더링 검증:

설정 렌더링 검증:

```bash
docker compose \
  --env-file deploy/ec2/.env.example \
  -f deploy/ec2/docker-compose.yml \
  config --quiet
```

현재 Backend와 Frontend는 골격 단계이므로 배포용 로컬 이미지가 아직 존재하지 않습니다. 두 애플리케이션이 scaffold되기 전에는 EC2 구성 전체의 `docker compose up` 성공을 #61의 완료 조건으로 보지 않습니다. `deploy/local`의 PostgreSQL과 MediaMTX는 독립적으로 실행할 수 있습니다.

애플리케이션 이미지가 준비된 후의 실행 기준은 다음과 같습니다.

```bash
docker compose --env-file deploy/ec2/.env -f deploy/ec2/docker-compose.yml up -d
docker compose --env-file deploy/ec2/.env -f deploy/ec2/docker-compose.yml ps
```

실제 시크릿이 든 `deploy/ec2/.env`는 Git에 추가하지 않습니다.

## 모듈 개발 순서

### 공통 계약

1. `common/protocol`에 의미와 호환성 정책을 기록합니다.
2. `common/schemas`에 OpenAPI/JSON Schema를 작성합니다.
3. `common/samples`에 정상·오류·재연결 샘플을 추가합니다.
4. 생산자와 소비자 테스트를 같은 MR에서 갱신합니다.

### Backend

1. Spring Boot 프로젝트와 Gradle wrapper를 추가합니다.
2. `/health`와 DB 연결을 먼저 검증합니다.
3. Flyway migration과 contract test를 추가합니다.
4. 실행 명령과 JDK 버전을 `backend/README.md`에 기록합니다.

### Frontend

1. Next.js App Router와 package manager lockfile을 추가합니다.
2. 샘플 데이터로 `/control`을 실행합니다.
3. API/WS 연결 실패와 재연결 상태를 먼저 구현합니다.
4. lint, typecheck, test, build 명령을 `frontend/README.md`에 기록합니다.

### Jetson

1. `./scripts/setup_jetson.sh`로 도구를 확인합니다.
2. 카메라와 LiDAR 장치 경로를 실측합니다.
3. 모터 없이 ROS topic과 샘플 bridge를 검증합니다.
4. 차량을 띄우고 물리 E-Stop을 확인한 뒤에만 모터 벤치 테스트를 수행합니다.

## 초기 통합 순서

1. 공통 메시지와 샘플 확정
2. 가짜 telemetry → Backend 수신·저장
3. Frontend 실시간 상태 표시
4. Jetson bridge를 가짜 telemetry 생산자 대신 연결
5. 카메라 단일 캡처와 LOCAL WebRTC
6. SLAM 수동 주행
7. Nav2 목표 주행
8. Frontier 탐사와 home 복귀
9. 사람 이벤트와 S3 업로드
10. 전체 시나리오 및 장애 주입

## 문제 해결

### Docker Engine에 연결할 수 없음

- Windows에서는 Docker Desktop의 Engine running 상태를 확인합니다.
- Linux에서는 Docker daemon과 현재 사용자의 권한을 확인합니다.
- `docker info`가 성공하기 전에는 Compose를 실행하지 않습니다.

### Job은 통과하지만 로컬에서 줄바꿈 오류가 발생함

- `.gitattributes`의 LF/CRLF 규칙을 유지합니다.
- 셸 스크립트는 LF, PowerShell은 CRLF checkout을 사용합니다.
- 대규모 줄바꿈 변경을 기능 변경과 같은 commit에 섞지 않습니다.

### 서비스는 켜졌지만 연결할 수 없음

- `docker compose ps`와 각 서비스 health를 확인합니다.
- Nginx upstream, WebSocket upgrade header, MediaMTX WHEP URL을 확인합니다.
- Browser console에 secret을 출력하지 않습니다.

# Sentinel UGV

재난·사고 현장을 자율 탐사하는 온디바이스 AIoT 기반 무인 지상 차량(UGV) 프로젝트입니다. Jetson Orin Nano에서 ROS 2, SLAM, Nav2, 객체 인식과 안전 제어를 수행하고, Spring Boot·Next.js 기반 관제 시스템에서 실시간 상태와 임무 이력을 제공합니다.

## 시스템 구성

- **Jetson**: 센서 수집, YOLO 사람 탐지, SLAM, 자율 탐사, 주행 및 안전 제어
- **Backend**: 임무·텔레메트리·이벤트 API, WebSocket, S3 연계
- **Frontend**: 실시간 영상·지도·상태 관제, 게임패드 수동 조작, 임무 이력
- **Infrastructure**: PostgreSQL/TimescaleDB, MediaMTX, Nginx, Docker Compose
- **Common**: 외부 프로토콜, 스키마, 샘플 메시지의 단일 기준점

## 저장소 구조

```text
.
├─ jetson/                  # 로봇 온보드 소프트웨어
│  ├─ ros2_ws/src/          # Sentinel ROS 2 패키지
│  ├─ streaming/            # 카메라 캡처·WebRTC 송출
│  ├─ models/               # 모델 메타데이터(가중치는 Git 제외)
│  ├─ config/               # 로봇 공통 설정
│  └─ tests/                # 온보드 단위·통합 테스트
├─ backend/                 # Spring Boot 관제 API
├─ frontend/                # Next.js 관제 웹
├─ common/                  # 공유 프로토콜·스키마·샘플
├─ deploy/                  # EC2, Nginx, MediaMTX 배포 설정
├─ scripts/                 # 설치·배포·점검·백업 스크립트
├─ hardware/                # CAD, 배선, BOM 산출물
├─ docs/                    # 아키텍처·규칙·테스트 문서
└─ .gitlab-ci.yml           # GitLab CI/CD 파이프라인
```

## 개발 시작

1. 작업 전 Jira 이슈를 생성합니다.
2. 최신 `develop`에서 `<type>/<scope>/<jira-key>-<description>` 형식의 브랜치를 만듭니다.
3. 담당 모듈의 `README.md`에 정의된 환경을 구성합니다.
4. 공통 메시지를 변경할 때는 `common/`과 관련 문서를 함께 갱신합니다.
5. 테스트 결과와 하드웨어 영향을 Merge Request에 기록합니다.

세부 규칙은 [Git 규칙](docs/conventions/git-convention.md), 전체 구조 설명은 [저장소 안내](docs/repository-structure.md)를 참고하세요.

## 안전 원칙

- 모터·E-Stop·전원 변경은 실제 장치 검증과 임베디드 담당 리뷰가 필요합니다.
- 프로그램 시작·종료·예외 및 제어 명령 TTL 초과 시 모터는 정지 상태여야 합니다.
- `.env`, 인증서, SSH 키, 모델 가중치와 클라우드 자격 증명은 커밋하지 않습니다.
- Jetson 배포 전 차량을 바닥에서 띄우고 물리 E-Stop 동작을 확인합니다.

## 프로젝트 상태

현재 저장소는 명세서 v0.9에 맞춘 초기 모노레포 골격입니다. 각 모듈의 실제 애플리케이션 생성 시 해당 디렉터리의 안내 파일을 유지하거나 실제 실행 문서로 대체합니다.

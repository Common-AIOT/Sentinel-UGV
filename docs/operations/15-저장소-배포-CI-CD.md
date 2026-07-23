<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 15. 저장소·배포·CI/CD [확정]
## 15.1 모노레포 구조
```text
sentinel-ugv/
├─ firmware/
│ └─ stm32/
│   ├─ Core/
│   ├─ Drivers/
│   ├─ protocol/
│   └─ tests/
├─ jetson/
│ ├─ ros2_ws/src/
│ │ ├─ sentinel_bringup/
│ │ ├─ sentinel_drive/
│ │ ├─ sentinel_perception/
│ │ ├─ sentinel_exploration/
│ │ ├─ sentinel_safety/
│ │ └─ sentinel_bridge/
│ ├─ streaming/
│ ├─ models/
│ ├─ config/
│ └─ tests/
├─ backend/
│ ├─ src/
│ ├─ db/migration/
│ └─ tests/
├─ frontend/
│ ├─ app/
│ ├─ components/
│ ├─ features/
│ └─ tests/
├─ common/
│ ├─ protocol/
│ ├─ schemas/
│ └─ samples/
├─ deploy/
│ ├─ ec2/docker-compose.yml
│ ├─ nginx/
│ └─ mediamtx/
├─ scripts/
│ ├─ setup_jetson.sh
│ ├─ deploy_jetson.sh
│ ├─ health_check.sh
│ └─ backup.sh
├─ hardware/
│ ├─ cad/
│ ├─ wiring/
│ └─ bom/
├─ docs/
├─ .gitlab-ci.yml
└─ README.md
```

## 15.2 브랜치 전략
| **브랜치** | **용도**                  |
|------------|---------------------------|
| main       | 시연 가능 상태. 배포 기준 |
| develop    | 통합 개발                 |
| feature/\* | 기능 단위 개발            |
| fix/\*     | 버그 수정                 |
| release/\* | 통합 시연 후보 안정화     |

- Merge Request에 관련 Jira 이슈, 테스트 방법, 하드웨어 영향, 롤백 방법을 작성한다.
- 모터·E-Stop·전원 변경은 최소 1명의 임베디드 담당 리뷰를 필수로 한다.

## 15.3 GitLab CI/CD 파이프라인
```yaml
stages:
1. lint
2. test
3. build
4. package
5. deploy_ec2
6. smoke_test
7. deploy_jetson_manual (확장)
```

| **대상**       | **자동 검사**                              | **배포**                   |
|----------------|--------------------------------------------|----------------------------|
| Backend        | Gradle test, static analysis, Docker build | main 병합 시 EC2 자동      |
| Frontend       | lint, typecheck, test, build               | main 병합 시 EC2 자동      |
| DB migration   | Flyway validate                            | EC2 배포 시 적용           |
| MediaMTX/Nginx | 설정 파일 검증                             | Docker Compose             |
| Jetson ROS2    | Python lint, unit test, config validation  | deploy_jetson.sh 수동 승인 |
| STM32 firmware | host protocol test, CRC vector, build      | 실기기 flash 수동 승인     |
| AI model       | 파일/해시/입력 테스트                      | 모델 교체 스크립트 수동    |

## 15.4 EC2 배포 흐름
```text
main merge
→ GitLab Runner 테스트
→ Backend/Frontend Docker 이미지 빌드
→ GitLab Container Registry push
→ EC2 SSH 또는 배포 Runner
→ docker compose pull
→ Flyway migration
→ docker compose up -d
→ /health 및 WebSocket smoke test
→ 실패 시 이전 이미지 태그로 rollback
```

## 15.5 Jetson 배포 흐름
```bash
./scripts/deploy_jetson.sh
1. 차량 정지 및 E-Stop 확인
2. Git commit/tag 확인
3. 의존성/환경 검증
4. rosdep install
5. colcon build
6. 설정 파일 검사
7. 기존 systemd 서비스 중지
8. 새 서비스 시작
9. LiDAR/카메라/모터 health check
10. 실패 시 이전 릴리스 symlink 복구
```

## 15.6 시크릿 관리
- AWS 키를 Jetson 코드나 저장소에 직접 저장하지 않는다.
- S3는 Presigned URL 방식으로 업로드하고 버킷을 private으로 유지한다.
- EC2의 DB 비밀번호·JWT/PIN·도메인 인증서는 GitLab CI 변수 또는 .env 배포 파일로 관리한다.
- .env, 모델 라이선스 파일, SSH 키는 Git에 커밋하지 않는다.

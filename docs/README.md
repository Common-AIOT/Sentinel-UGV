# Sentinel UGV 개발 문서

이 디렉터리는 [통합 프로젝트 명세서 v1.0-rc1](specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md)을 기준으로 개발자가 실행 가능한 문서를 영역별로 나눈 문서 모음입니다. 전체본이 문서 기준선이며, 장별 문서는 전체본에서 자동 생성합니다.

## 문서 지도

| 영역 | 문서 | 용도 |
|---|---|---|
| 전체 명세 | [specifications/README.md](specifications/README.md) | v1.0-rc1 전체본, 부록과 동기화 방법 |
| 제품 | [product/README.md](product/README.md) | 목표, 범위, 시나리오, 일정과 역할 |
| 하드웨어 | [hardware/README.md](hardware/README.md) | 기구, 전원, 배선, STM32와 캘리브레이션 |
| Jetson | [jetson/README.md](jetson/README.md) | ROS 2, AI, 센서 융합, SLAM, Nav2와 미디어 |
| 백엔드 | [backend/README.md](backend/README.md) | API, 데이터베이스, S3와 Outbox |
| 프론트엔드 | [frontend/README.md](frontend/README.md) | 관제 웹과 조이스틱 UX |
| 운영 | [operations/README.md](operations/README.md) | 배포, 보안, 모니터링과 장애 복구 |
| 아키텍처 | [architecture/README.md](architecture/README.md) | 시스템 경계, 런타임, 상태와 통신 설계 |
| 테스트 | [testing/README.md](testing/README.md) | 단계별 검증, 합격 기준, 증적과 인수 시험 |
| 협업 | [conventions/Git-브랜치-커밋-MR-컨벤션.md](conventions/Git-브랜치-커밋-MR-컨벤션.md) | 브랜치, 커밋, Merge Request 규칙 |

외부 시스템 간 기계 검증 가능한 계약은 [`common/`](../common/README.md)을 단일 기준점으로 사용합니다.

## 결정 상태

- **확정**: 구현 기준입니다. 변경 시 같은 Merge Request에서 영향 문서와 테스트를 갱신합니다.
- **잠정 확정**: 초기값으로 사용하되 실측 결과에 따라 ADR 또는 변경 이력을 남기고 수정할 수 있습니다.
- **TBD**: 측정이나 부품 선정 전에는 임의의 값으로 확정하지 않습니다.
- **확장**: MVP 완료 전 필수 일정에 포함하지 않습니다.

## 유지 원칙

1. 코드와 문서가 다르면 현재 동작을 먼저 검증하고, 차이의 원인과 안전 영향을 전체 명세서에 기록합니다.
2. API나 이벤트 필드를 변경하면 `common/`의 스키마, 샘플, 생산자, 소비자를 함께 변경합니다.
3. 모터, E-Stop, 전원, 명령 TTL 변경은 안전 정책과 실제 장치 테스트 결과를 함께 갱신합니다.
4. 비밀 값, 실제 장치 토큰, 사설 URL과 개인정보는 문서 및 샘플에 기록하지 않습니다.
5. 성능 수치는 측정 환경, commit, 하드웨어 버전과 함께 기록합니다.
6. `GENERATED FILE` 표시가 있는 장별 문서는 직접 수정하지 않습니다. 전체본 수정 후 `scripts/docs/split-integrated-spec.ps1`을 실행하고 `-Check`로 동기화를 검증합니다.

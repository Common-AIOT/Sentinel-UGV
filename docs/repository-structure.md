# 저장소 구조와 소유권

이 문서는 통합 프로젝트 명세서 v0.9의 모노레포 구조를 개발자가 바로 사용할 수 있는 경계로 정리합니다.

| 경로 | 책임 | 대표 산출물 |
|---|---|---|
| `jetson/` | 로봇 로컬 인지·자율주행·제어 | ROS 2 패키지, 모델 설정, 스트리밍 코드 |
| `backend/` | 관제 API와 영속화 | Spring Boot 애플리케이션, Flyway migration |
| `frontend/` | 운영자 관제 UI | Next.js App Router, UI 컴포넌트, 기능 모듈 |
| `common/` | 시스템 간 계약 | JSON/OpenAPI/AsyncAPI/ROS 메시지 명세, 샘플 |
| `deploy/` | 서버 런타임 | Docker Compose, Nginx, MediaMTX 설정 |
| `scripts/` | 반복 가능한 운영 절차 | Jetson 설치·배포, 상태 점검, 백업 |
| `hardware/` | 하드웨어 설계 기준 | CAD 원본, 배선도, BOM |
| `docs/` | 의사결정과 검증 근거 | 아키텍처, 규칙, 테스트 결과 |

## 의존 방향

```text
frontend ── REST/WebSocket ──> backend
                                  │
jetson ─── WebSocket/REST ────────┘
  │
  ├─ ROS 2 내부 통신 ──> drive / perception / exploration / safety
  └─ WebRTC ───────────> browser 또는 MediaMTX

frontend / backend / jetson ──> common의 계약을 구현
deploy ──> backend / frontend의 빌드 산출물을 실행
```

`common/`은 한 모듈의 구현 세부사항을 가져오지 않습니다. API나 이벤트 필드 변경은 스키마, 생산자, 소비자와 샘플을 같은 Merge Request에서 갱신합니다.

## 디렉터리 운영 원칙

- 비어 있는 골격 디렉터리는 목적을 설명하는 `README.md`로 추적합니다.
- 빌드 산출물과 장치별 설정은 커밋하지 않습니다.
- 대용량 모델 가중치와 녹화 영상은 Git 대신 릴리스 저장소 또는 S3를 사용하고 해시와 취득 방법만 기록합니다.
- 하드웨어 파일은 원본과 내보낸 결과를 구분하고, BOM 변경에는 부품 버전과 결정일을 기록합니다.
- 모터 또는 안전 정책을 변경하는 코드는 `sentinel_safety`를 우회하지 않습니다.

## 새 모듈 추가 체크리스트

- 실행 방법과 책임 범위를 모듈 `README.md`에 기록했는가
- 설정 예시는 실제 시크릿 없이 제공되는가
- 단위 테스트 또는 검증 명령이 있는가
- 외부 계약 변경을 `common/`에 반영했는가
- 로그에 timestamp, component, level, error code가 포함되는가
- 장애 시 안전 동작과 롤백 방법을 Merge Request에 적었는가

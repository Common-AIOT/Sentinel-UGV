# Sentinel UGV 개발 문서

이 디렉터리는 통합 프로젝트 명세서 v0.9(2026-07-17)를 개발자가 실행 가능한 기준으로 나눈 문서 모음입니다. 종합 명세서를 반복해서 복사하지 않고, 구현 전에 합의가 필요한 경계와 검증 방법만 관리합니다.

## 문서 지도

| 영역 | 문서 | 용도 |
|---|---|---|
| 저장소 | [repository-structure.md](repository-structure.md) | 모노레포 경계와 소유권 |
| 아키텍처 | [architecture/system-context.md](architecture/system-context.md) | 시스템 경계, 외부 의존성, 데이터 소유권 |
| 아키텍처 | [architecture/robot-runtime.md](architecture/robot-runtime.md) | Jetson ROS 2 런타임과 안전 제어 체인 |
| 아키텍처 | [architecture/control-and-telemetry.md](architecture/control-and-telemetry.md) | REST, WebSocket, WebRTC 데이터 흐름 |
| 안전 | [architecture/safety-policy.md](architecture/safety-policy.md) | 상태, 우선순위, 장애 시 fail-safe 동작 |
| 개발 | [development/local-setup.md](development/local-setup.md) | 공통 도구, 환경 변수, 포트, 실행 순서 |
| 테스트 | [testing/test-strategy.md](testing/test-strategy.md) | 단계별 검증, 합격 기준, 증적 형식 |
| 협업 | [conventions/git-convention.md](conventions/git-convention.md) | 브랜치, 커밋, Merge Request 규칙 |

외부 시스템 간 기계 검증 가능한 계약은 [`common/`](../common/README.md)을 단일 기준점으로 사용합니다.

## 결정 상태

- **확정**: 구현 기준입니다. 변경 시 같은 Merge Request에서 영향 문서와 테스트를 갱신합니다.
- **잠정 확정**: 초기값으로 사용하되 실측 결과에 따라 ADR 또는 변경 이력을 남기고 수정할 수 있습니다.
- **TBD**: 측정이나 부품 선정 전에는 임의의 값으로 확정하지 않습니다.
- **확장**: MVP 완료 전 필수 일정에 포함하지 않습니다.

## 유지 원칙

1. 코드와 문서가 다르면 현재 동작을 먼저 검증하고, 차이의 원인과 안전 영향을 기록합니다.
2. API나 이벤트 필드를 변경하면 `common/`의 스키마, 샘플, 생산자, 소비자를 함께 변경합니다.
3. 모터, E-Stop, 전원, 명령 TTL 변경은 안전 정책과 실제 장치 테스트 결과를 함께 갱신합니다.
4. 비밀 값, 실제 장치 토큰, 사설 URL과 개인정보는 문서 및 샘플에 기록하지 않습니다.
5. 성능 수치는 측정 환경, commit, 하드웨어 버전과 함께 기록합니다.

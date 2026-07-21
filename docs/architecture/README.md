# Architecture

시스템 컨텍스트, 로봇 런타임, 데이터 흐름과 안전 정책을 보관합니다.

- [system-context.md](system-context.md): 시스템 경계, 배치 프로필, 데이터 소유권
- [robot-runtime.md](robot-runtime.md): ROS 2 처리 파이프라인과 노드 경계
- [control-and-telemetry.md](control-and-telemetry.md): REST, WebSocket, WebRTC 흐름
- [safety-policy.md](safety-policy.md): 상태 머신, 제어 우선순위, 장애 정책

새로운 기술 선택이나 기존 확정 결정을 변경할 때는 `adr/NNNN-short-title.md` 형식의 Architecture Decision Record를 추가합니다.

아키텍처 결정에는 배경, 선택지, 결정, 장단점, 검증 방법과 변경일을 기록합니다.

# Frontend

Next.js App Router 기반 통합 관제 웹입니다. 실시간 영상, 지도, 상태, 게임패드 제어와 과거 임무 조회를 담당합니다.

- `app/`: 라우트, 레이아웃과 서버/클라이언트 경계
- `components/`: 재사용 가능한 표현 컴포넌트
- `features/`: mission, telemetry, control, streaming 등 도메인 기능
- `tests/`: 단위, 컴포넌트와 E2E 테스트

브라우저에서 노출 가능한 값만 `NEXT_PUBLIC_` 환경 변수로 정의합니다.

# Frontend

Next.js **App Router** 기반 통합 관제 웹(GCS)입니다. 실시간 영상, LiDAR 점유 지도, 상태·센서, 게임패드 제어와 과거 임무(탐지·블랙박스) 조회를 담당합니다.

## 스택

- Next.js 14 (App Router) · React 18 · TypeScript
- Tailwind CSS v4 (`@tailwindcss/postcss`)
- 아이콘 `lucide-react`, 토스트 `sonner`, 실시간 통신 `@stomp/stompjs`
- 패키지 매니저: **npm**

## 구조

- `app/`: 라우트·레이아웃과 서버/클라이언트 경계
  - `page.tsx`(/ GCS 메인), `detections/page.tsx`, `blackbox/page.tsx`
  - `layout.tsx`, `providers.tsx`(전역 Context·Toaster), `globals.css`
- `components/`: 재사용 가능한 표현 컴포넌트 (필요 시 `npx shadcn add <name>`로 추가)
- `features/`: 도메인 기능
  - `robot/`: `RobotContext`(중앙 상태), `mockData`(목 데이터)
  - `control/`(조이스틱), `telemetry/`(상태·센서), `streaming/`(영상), `mapping/`(LiDAR), `detection/`
- `tests/`: 단위·컴포넌트·E2E 테스트

## 실행

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # 프로덕션 빌드 + 타입체크
npm run lint
```

## 백엔드 연동

`features/robot/RobotContext.tsx`의 `USE_MOCK`가 `true`면 목 시뮬레이션으로 동작합니다. 실제 백엔드(WebSocket/STOMP `ws://localhost:8080`, REST `/api/...`) 연동은 `USE_MOCK`를 끄고 `API` 엔드포인트를 배선하며 진행합니다. 브라우저에 노출 가능한 값만 `NEXT_PUBLIC_` 환경 변수로 정의합니다.

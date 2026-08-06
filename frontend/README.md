# Frontend

Next.js **App Router** 기반 통합 관제 웹(GCS)입니다. 실시간 영상, LiDAR 점유 지도, 상태·센서, 운행 모드 전환과 임무 명령, 과거 임무(블랙박스) 조회를 담당합니다. 조종 입력은 없습니다 — 수동 조종은 모바일 앱이 맡습니다(05장 28장).

## 스택

- Next.js 14 (App Router) · React 18 · TypeScript
- Tailwind CSS v4 (`@tailwindcss/postcss`)
- 아이콘 `lucide-react`, 토스트 `sonner`, 실시간 통신 `@stomp/stompjs`
- 패키지 매니저: **npm**

## 구조

- `app/`: 라우트·레이아웃과 서버/클라이언트 경계
  - `page.tsx`(/ GCS 메인), `blackbox/page.tsx`, `blackbox-experiment/page.tsx`
  - `layout.tsx`, `providers.tsx`(전역 Context·Toaster), `globals.css`
- `components/`: 재사용 가능한 표현 컴포넌트 (필요 시 `npx shadcn add <name>`로 추가)
- `features/`: 도메인 기능
  - `robot/`: `RobotContext`(중앙 상태), `mockData`(목 데이터)
  - `telemetry/`(상태·센서·모드 전환·임무 명령), `streaming/`(영상), `mapping/`(LiDAR)
- `tests/`: 단위·컴포넌트·E2E 테스트
- `docs/`: 화면 설계 산출물. [`wireframe.md`](docs/wireframe.md)는 화면 4종의 배치와 각 요소의 역할을 담은 와이어프레임이며 GitLab에서 바로 렌더링된다

> 관제 웹에는 조종 입력이 없다. 운행 모드 전환은 `features/telemetry/ModeRow.tsx`,
> 임무 명령은 `features/telemetry/CommandBar.tsx`에 있고 실제 조종은 모바일 앱이
> 맡는다(S15P11A301-196·197).

## 실행

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # 프로덕션 빌드 + 타입체크
npm run lint
```

## 백엔드 연동

`features/robot/RobotContext.tsx`의 `USE_MOCK`가 `true`면 목 시뮬레이션으로 동작합니다. 실제 백엔드 연동은 `USE_MOCK`를 끄고 `API` 엔드포인트를 배선하며 진행합니다. 브라우저에 노출 가능한 값만 `NEXT_PUBLIC_` 환경 변수로 정의합니다.

> ⚠️ **`USE_MOCK`는 환경 변수가 아니라 `export const`로 코드에 박힌 상수다**(`RobotContext.tsx:27`).
> 끄려면 코드를 고쳐 배포해야 하며, 배포 설정으로는 갈리지 않는다. 시연·심사용 배포에서
> 목 데이터가 섞이지 않도록 배포 전에 값을 확인한다.

백엔드 주소는 환경 변수로 주입합니다. 값이 없으면 로컬 개발 기준(`http://localhost:8080`)으로 동작합니다.

| 변수 | 운영 값 | 용도 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://api.sentinel-ugv.xyz` | REST API |
| `NEXT_PUBLIC_WS_URL` | `wss://api.sentinel-ugv.xyz/ws` | STOMP/WebSocket |

로컬은 `frontend/.env.local`에 두고, 배포는 **Vercel 프로젝트 환경 변수**(Settings → Environment Variables)에 등록합니다. CI는 `vercel deploy --prod`만 호출하므로 GitLab 변수로는 주입되지 않습니다.

백엔드는 이 출처를 CORS 허용 목록(`app.cors.allowed-origins`)에 두어야 브라우저 호출이 통과합니다.

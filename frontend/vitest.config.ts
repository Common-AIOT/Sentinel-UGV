/**
 * 프런트엔드 단위 시험 설정 (S15P11A301-223).
 *
 * `tests/README.md`가 이 자리를 비워 두고 있었고 러너가 없었다. CI는 `tsc`와
 * `next build`만 돌리는데 그것들은 타입과 빌드만 본다 — **틀려도 화면이 정상으로
 * 보이는 로직**은 하나도 잡지 못한다.
 *
 * 지도 좌표 변환이 그 대표다. y를 뒤집지 않아도 궤적이 지도 위에 그려지고,
 * 미탐사 값 205를 임계값으로 판정하면 미탐사 영역이 탐사된 바닥으로 그려진다.
 * 둘 다 벽은 그대로 맞아서 눈으로 알 수 없다.
 *
 * jsdom을 쓰지 않는다. 시험 대상은 DOM을 모르는 순수 함수이고, 환경을 끌어오면
 * 설치 시간만 늘어난다. 컴포넌트 시험이 필요해지면 그때 추가한다.
 */
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    // tsconfig의 paths(`@/*` → `./*`)와 같아야 한다. 어긋나면 시험만 실패한다.
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});

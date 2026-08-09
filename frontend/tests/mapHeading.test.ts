import { describe, expect, it } from "vitest";
import {
  arrowRotationFromMapYaw,
  arrowScreenVector,
  mapYawDegrees,
} from "@/lib/mapHeading";

/**
 * 화살표 방향은 **화면 벡터로** 고정한다 (S15P11A301-364).
 *
 * 종전 시험은 `arrowRotationFromMapYaw(yaw) === yaw` 처럼 부호만 비교했는데,
 * 그것은 「화면에서 어디를 가리키는가」를 말해 주지 않아 규칙이 한 번 왕복한
 * 뒤에도 통과했다. 캔버스 규약(x 오른쪽, y 아래)에서 실제 벡터를 검사한다.
 */
describe("관제 화살표가 화면에서 가리키는 방향", () => {
  it("map +x(방위 0°)면 화면 오른쪽을 가리킨다", () => {
    const v = arrowScreenVector(0);
    expect(v.x).toBeCloseTo(1, 12);
    expect(v.y).toBeCloseTo(0, 12);
  });

  it("map +y(반시계 +90°)면 화면 위쪽을 가리킨다 — 지도가 상하 반전이라 y는 음수다", () => {
    const v = arrowScreenVector(Math.PI / 2);
    expect(v.x).toBeCloseTo(0, 12);
    expect(v.y).toBeCloseTo(-1, 12); // 캔버스에서 음수 y = 위쪽
  });

  it("map -y(시계 -90°)면 화면 아래쪽을 가리킨다", () => {
    const v = arrowScreenVector(-Math.PI / 2);
    expect(v.x).toBeCloseTo(0, 12);
    expect(v.y).toBeCloseTo(1, 12);
  });

  it("map -x(180°)면 화면 왼쪽을 가리킨다", () => {
    const v = arrowScreenVector(Math.PI);
    expect(v.x).toBeCloseTo(-1, 12);
    expect(v.y).toBeCloseTo(0, 12);
  });

  it("실기동에서 확인한 사례: 방위 11.6°는 화면에서 위로 기운다", () => {
    // 2026-08-09 실기동. 이 값으로 「로봇이 벽으로 간다」고 보였고, 실제 격자에서는
    // 정면 3m 자유·후방 3m 점유였다.
    const v = arrowScreenVector((11.6 * Math.PI) / 180);
    expect(v.x).toBeGreaterThan(0); // 오른쪽 성분
    expect(v.y).toBeLessThan(0); // 위쪽 성분 — 종전 코드는 여기가 양수였다
  });
});

describe("회전각과 표시각", () => {
  it.each([
    { yaw: 0, degrees: 0 },
    { yaw: Math.PI / 4, degrees: 45 },
    { yaw: -Math.PI / 4, degrees: -45 },
    { yaw: Math.PI / 2, degrees: 90 },
    { yaw: -Math.PI / 2, degrees: -90 },
    { yaw: Math.PI, degrees: 180 },
  ])("$degrees도: 회전은 반대 부호, 표시각은 그대로", ({ yaw, degrees }) => {
    // 화면 회전은 상하 반전 때문에 부호가 뒤집힌다.
    expect(arrowRotationFromMapYaw(yaw)).toBeCloseTo(-yaw, 12);
    // 사람이 읽는 방위는 map 기준 그대로다 — 뒤집지 않는다.
    expect(mapYawDegrees(yaw)).toBeCloseTo(degrees, 12);
  });
});

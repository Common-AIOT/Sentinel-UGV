import { describe, expect, it } from "vitest";
import {
  arrowRotationFromMapYaw,
  mapYawDegrees,
} from "@/lib/mapHeading";

describe("관제 화살표 yaw 반영", () => {
  it("시계 방향 -90도를 같은 부호와 크기로 전달한다", () => {
    const yaw = -Math.PI / 2;

    expect(arrowRotationFromMapYaw(yaw)).toBeCloseTo(-Math.PI / 2, 12);
    expect(mapYawDegrees(yaw)).toBeCloseTo(-90, 12);
  });

  it("반시계 방향 +90도를 같은 부호와 크기로 전달한다", () => {
    const yaw = Math.PI / 2;

    expect(arrowRotationFromMapYaw(yaw)).toBeCloseTo(Math.PI / 2, 12);
    expect(mapYawDegrees(yaw)).toBeCloseTo(90, 12);
  });

  it.each([
    { yaw: 0, degrees: 0 },
    { yaw: Math.PI, degrees: 180 },
    { yaw: -Math.PI, degrees: -180 },
    { yaw: Math.PI / 4, degrees: 45 },
    { yaw: -Math.PI / 4, degrees: -45 },
  ])("$degrees도를 정확히 표시한다", ({ yaw, degrees }) => {
    expect(arrowRotationFromMapYaw(yaw)).toBe(yaw);
    expect(mapYawDegrees(yaw)).toBeCloseTo(degrees, 12);
  });
});

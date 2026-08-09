import { describe, expect, it } from "vitest";

import { controlModeFromLatest } from "@/features/telemetry/sensorReading";

import type { TelemetryLatest } from "@/lib/api";

/**
 * 제어 모드 판정 (S15P11A301-350).
 *
 * 여기서 지키는 것은 **3값이 2값으로 뭉개지지 않는 것** 하나다. 2026-08-08 실기동에서
 * 관제 화면이 14분간 「자율」을 보여줬는데, 로봇은 그동안 수동이었다. 원인은 모드를
 * `missions.status` 에서 파생시킨 것이었고 — 임무가 닫히면 그 칸이 갱신되지 않는다 —
 * 그래서 「모름」이 「자율」로 떨어졌다.
 *
 * `null` 을 AUTO 로 바꾸는 코드가 이 경로 어디에 들어와도 그 사고가 그대로 재발한다.
 */
const NOW = Date.parse("2026-08-08T12:00:00.000Z");
const iso = (agoMs: number) => new Date(NOW - agoMs).toISOString();

function latest(over: Partial<TelemetryLatest> = {}): TelemetryLatest {
  return {
    controlMode: "AUTO",
    environmentTime: iso(1_000),
    temperature: 24.7,
    humidity: 60.0,
    mcuTime: iso(1_000),
    mcuConnected: true,
    motorLinkOk: true,
    recorderOk: null,
    recorderLastFailure: null,
    poseTime: iso(1_000),
    linearVelocity: 0,
    angularVelocity: 0,
    ...over,
  };
}

describe("controlModeFromLatest", () => {
  it("신선한 MANUAL·AUTO 는 그대로 통과한다", () => {
    expect(controlModeFromLatest(latest({ controlMode: "MANUAL" }), NOW)).toBe("MANUAL");
    expect(controlModeFromLatest(latest({ controlMode: "AUTO" }), NOW)).toBe("AUTO");
  });

  it("서버가 null 을 주면 null 이다 — AUTO 로 뭉개지 않는다", () => {
    // 이 티켓의 핵심. 「모름」과 「자율」이 같은 값이 되면 수동 조종 중에도 화면이
    // 「자율」을 띄운다.
    expect(controlModeFromLatest(latest({ controlMode: null }), NOW)).toBeNull();
  });

  it("키 자체가 없는 응답(백엔드 배포 전)에도 null 이다", () => {
    const before = {
      environmentTime: iso(1_000),
      mcuTime: iso(1_000),
      mcuConnected: true,
    } as TelemetryLatest;
    const r = controlModeFromLatest(before, NOW);
    expect(r).toBeNull();
    // undefined 면 화면의 「모름」 분기가 뚫린다 — motionFromLatest 가 겪은 그 문제다.
    expect(r).not.toBeUndefined();
  });

  it("오래된 값은 null 이다 — 죽은 로봇을 수동으로도 자율로도 보여주지 않는다", () => {
    const stale = latest({ controlMode: "MANUAL", mcuTime: iso(61_000) });
    expect(controlModeFromLatest(stale, NOW)).toBeNull();
  });

  it("mcuTime 이 없으면 null 이다", () => {
    expect(controlModeFromLatest(latest({ mcuTime: null }), NOW)).toBeNull();
  });

  it("모르는 문자열은 null 이다", () => {
    // 젯슨이 모드를 하나 더 늘려도 화면이 그것을 자율로 오해하지 않게 한다.
    const odd = latest({ controlMode: "TELEOP" as unknown as "AUTO" });
    expect(controlModeFromLatest(odd, NOW)).toBeNull();
  });
});

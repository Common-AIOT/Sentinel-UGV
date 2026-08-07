/**
 * 주행 지표 판정 시험 (S15P11A301-300).
 *
 * 첫 케이스가 실제로 터진 버그다 — 백엔드 배포 전 응답에는 이 키들이 아예 없어
 * undefined 가 왔고, 화면이 `value === null` 로만 검사해 `undefined.toFixed()` 로
 * 페이지가 죽었다. 응답에 필드를 더할 때마다 같은 위험이 생기므로 시험으로 고정한다.
 */

import { describe, expect, it } from "vitest";
import type { TelemetryLatest } from "@/lib/api";
import { MOTION_FRESH_MS, motionFromLatest } from "@/features/telemetry/motionReading";

const NOW = Date.parse("2026-08-06T04:00:00.000Z");
const FRESH_ISO = new Date(NOW - 1_000).toISOString();

/** 배포 후 응답. 필드가 전부 있다. */
function full(): TelemetryLatest {
  return {
    environmentTime: FRESH_ISO, temperature: 25, humidity: 60,
    mcuTime: FRESH_ISO, mcuConnected: true,
    recorderOk: true, recorderLastFailure: null,
    poseTime: FRESH_ISO, linearVelocity: 0.24, angularVelocity: -0.05,
  };
}

describe("motionFromLatest", () => {
  it("배포 전 응답(키 자체가 없음)에도 결측으로 처리한다", () => {
    const old = {
      environmentTime: FRESH_ISO, temperature: 25, humidity: 60,
      mcuTime: FRESH_ISO, mcuConnected: true,
    } as TelemetryLatest;
    const r = motionFromLatest(old, NOW);
    expect(r.linearVelocity).toBeNull();
    expect(r.angularVelocity).toBeNull();
    // null 이어야 한다 — undefined 면 화면의 결측 표시가 뚫린다.
    expect(r.linearVelocity).not.toBeUndefined();
  });

  it("신선한 값은 그대로 통과한다 — 음수 각속도 포함", () => {
    const r = motionFromLatest(full(), NOW);
    expect(r).toEqual({
      linearVelocity: 0.24, angularVelocity: -0.05, updatedAt: Date.parse(FRESH_ISO),
    });
  });

  it("0 은 결측이 아니다 — 정지 상태의 실측값이다", () => {
    const stopped = { ...full(), linearVelocity: 0, angularVelocity: 0 };
    const r = motionFromLatest(stopped, NOW);
    expect(r.linearVelocity).toBe(0);
    expect(r.angularVelocity).toBe(0);
  });

  it("오래된 값은 결측이다 — 멈춘 값을 현재값처럼 보여주지 않는다", () => {
    const stale = { ...full(), poseTime: new Date(NOW - MOTION_FRESH_MS - 1).toISOString() };
    const r = motionFromLatest(stale, NOW);
    expect(r.linearVelocity).toBeNull();
    expect(r.updatedAt).toBeNull();
  });

  it("poseTime 이 없으면 결측이다", () => {
    const r = motionFromLatest({ ...full(), poseTime: null }, NOW);
    expect(r.linearVelocity).toBeNull();
    expect(r.updatedAt).toBeNull();
  });

  it("신선도는 poseTime 으로 본다 — 온습도 시각이 낡아도 주행은 살아 있다", () => {
    // 두 값은 다른 하이퍼테이블에서 온다. environmentTime 으로 판정하면 어긋난다.
    const envDead = { ...full(), environmentTime: new Date(NOW - 3_600_000).toISOString() };
    expect(motionFromLatest(envDead, NOW).linearVelocity).toBe(0.24);
  });
});

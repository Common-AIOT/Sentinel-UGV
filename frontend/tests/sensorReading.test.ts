import { describe, expect, it } from "vitest";

import {
  SENSOR_FRESH_MS,
  sensorsFromLatest,
  sensorsFromMissionPoint,
} from "@/features/telemetry/sensorReading";
import type { TelemetryLatest, TelemetryPoint } from "@/lib/api";

/**
 * 센서 표시값 시험 (S15P11A301-322).
 *
 * 지키는 것은 둘이다. **오래된 값을 살아 있는 것처럼 보여주지 않는 것**과,
 * 그 반대로 **살아 있는 값을 감추지 않는 것**. 후자가 실제 결함이었다.
 */

const NOW = Date.parse("2026-08-07T00:40:00.000Z");
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();

/**
 * 녹화기 두 필드는 S15P11A301-311 이 **필수**로 더했다(`boolean | null`, 옵셔널이
 * 아니다). 이 시험은 그 전에 갈라져 나와 두 칸이 빈 채로 있었고, 각 브랜치의
 * 파이프라인에서는 서로를 보지 못해 둘 다 통과했다 — develop 에서 만나서야
 * `tsc` 가 막았다(S15P11A301-325).
 *
 * 값은 null 이다. 이 시험이 지키는 것은 센서 신선도이지 녹화기가 아니고, null 은
 * 「판정 근거 없음」이라 어느 쪽으로도 결론을 밀지 않는다. 녹화기 판정은
 * recorderStatus 시험이 따로 갖는다.
 */
const NO_RECORDER = { recorderOk: null, recorderLastFailure: null };

function latest(over: Partial<TelemetryLatest> = {}): TelemetryLatest {
  return {
    environmentTime: iso(1_000),
    temperature: 24.7,
    humidity: 60.0,
    mcuTime: iso(1_000),
    mcuConnected: true,
    ...NO_RECORDER,
    poseTime: iso(1_000),
    linearVelocity: 0,
    angularVelocity: 0,
    ...over,
  };
}

function point(over: Partial<TelemetryPoint> = {}): TelemetryPoint {
  return {
    time: "2026-08-07T00:39:50.000Z",
    cpu: null, gpu: null, memory: null, jetsonTemp: null, battery: null,
    temperature: 23.1,
    humidity: 55.0,
    linearVelocity: 0.1,
    angularVelocity: 0,
    mcuConnected: true,
    ...NO_RECORDER,
    ...over,
  };
}

describe("sensorsFromLatest", () => {
  it("신선한 값은 그대로 쓴다", () => {
    const s = sensorsFromLatest(latest(), NOW);
    expect(s.temperature).toBe(24.7);
    expect(s.humidity).toBe(60.0);
    expect(s.mcuConnected).toBe(true);
    expect(s.updatedAt).toBe(Date.parse(iso(1_000)));
  });

  it("60초 넘은 온습도는 결측이다", () => {
    const s = sensorsFromLatest(latest({ environmentTime: iso(SENSOR_FRESH_MS + 1) }), NOW);
    expect(s.temperature).toBeNull();
    expect(s.humidity).toBeNull();
    expect(s.updatedAt).toBeNull();
    // MCU 는 자기 시각으로 따로 판정한다 — 함께 죽이지 않는다.
    expect(s.mcuConnected).toBe(true);
  });

  it("온습도와 MCU 를 각각 판정한다", () => {
    // 두 값이 다른 하이퍼테이블에서 오므로 한쪽만 낡을 수 있다.
    const s = sensorsFromLatest(latest({ mcuTime: iso(SENSOR_FRESH_MS + 1) }), NOW);
    expect(s.temperature).toBe(24.7);
    expect(s.mcuConnected).toBeNull();
  });

  it("시각이 없으면 결측이다", () => {
    const s = sensorsFromLatest(latest({ environmentTime: null, mcuTime: null }), NOW);
    expect(s.temperature).toBeNull();
    expect(s.mcuConnected).toBeNull();
  });

  it("배포 전 응답의 undefined 를 결측으로 받는다", () => {
    // 백엔드에 필드가 추가되기 전 배포본은 키 자체를 안 보낸다(#300 에서 겪었다).
    const partial = { environmentTime: iso(1_000), mcuTime: iso(1_000) } as unknown as TelemetryLatest;
    const s = sensorsFromLatest(partial, NOW);
    expect(s.temperature).toBeNull();
    expect(s.humidity).toBeNull();
    expect(s.mcuConnected).toBeNull();
  });
});

describe("sensorsFromMissionPoint", () => {
  it("버킷 값을 그대로 쓴다 — 조회 창이 이미 최근 60초다", () => {
    const s = sensorsFromMissionPoint(point());
    expect(s.temperature).toBe(23.1);
    expect(s.humidity).toBe(55.0);
    expect(s.mcuConnected).toBe(true);
    expect(s.updatedAt).toBe(Date.parse("2026-08-07T00:39:50.000Z"));
  });

  it("구간에 측정이 없었으면 null 이고 0 이 아니다", () => {
    const s = sensorsFromMissionPoint(point({ temperature: null, humidity: null, mcuConnected: null }));
    expect(s.temperature).toBeNull();
    expect(s.humidity).toBeNull();
    expect(s.mcuConnected).toBeNull();
  });
});

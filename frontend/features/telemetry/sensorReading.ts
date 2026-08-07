import type { TelemetryLatest, TelemetryPoint } from "@/lib/api";
import type { SensorReading } from "@/features/robot/mockData";

/**
 * 센서 표시값 만들기 (S15P11A301-322).
 *
 * `motionFromLatest` 와 같은 이유로 화면 밖에 둔다 — 신선도 판정과 결측 처리가
 * 컴포넌트 안에 있으면 시험이 닿지 않아 조용히 썩는다. 실제로 그렇게 썩었다:
 * 임무 telemetry 가 비었을 때 결측을 세우고 끝내는 분기가 **살아 있는 값을
 * 감췄다**(아래).
 *
 * 두 출처가 있고 성격이 다르다.
 *
 * - **임무 telemetry**: 버킷 집계다. 조회 창이 이미 최근 60초라 그 안의 값은
 *   신선하다 — 여기서 시각을 다시 보지 않는다.
 * - **임무 무관 최신값**(`/telemetry/latest`): 시각이 그룹별로 따로 온다
 *   (온습도는 environment_metrics, MCU 는 robot_metrics). 그래서 **각각** 판정한다.
 */
export const SENSOR_FRESH_MS = 60_000;

/**
 * 임무 버킷 → 표시값. `null` 은 그 구간에 측정이 없었다는 뜻이고 0 과 다르다.
 */
export function sensorsFromMissionPoint(point: TelemetryPoint): SensorReading {
  return {
    temperature: point.temperature ?? null,
    humidity: point.humidity ?? null,
    mcuConnected: point.mcuConnected ?? null,
    updatedAt: Date.parse(point.time),
  };
}

/**
 * 최신값 → 표시값. 60초 넘게 오래된 값은 결측으로 만든다 — 죽은 센서를 살아
 * 있는 것처럼 보여주지 않는다(젯슨 6초 null 규칙과 같은 원칙).
 *
 * `?? null` 은 방어가 아니라 필요다 — 백엔드에 필드가 추가되기 전 배포본은 키
 * 자체를 안 보내 `undefined` 가 온다(`motionFromLatest` 가 겪은 그 문제다).
 */
export function sensorsFromLatest(d: TelemetryLatest, now: number): SensorReading {
  const envTime = d.environmentTime == null ? null : Date.parse(d.environmentTime);
  const mcuTime = d.mcuTime == null ? null : Date.parse(d.mcuTime);
  const envFresh = envTime !== null && now - envTime <= SENSOR_FRESH_MS;
  const mcuFresh = mcuTime !== null && now - mcuTime <= SENSOR_FRESH_MS;
  return {
    temperature: envFresh ? d.temperature ?? null : null,
    humidity: envFresh ? d.humidity ?? null : null,
    mcuConnected: mcuFresh ? d.mcuConnected ?? null : null,
    updatedAt: envFresh ? envTime : null,
  };
}

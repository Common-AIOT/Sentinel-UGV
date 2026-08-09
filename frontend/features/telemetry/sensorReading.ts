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

/**
 * 최신값 → 모터 보드 링크 (S15P11A301-317).
 *
 * `mcuConnected` 와 **다른 보드**다 — 그쪽은 엔코더를 내는 센서 ESP32 이고 이 값은
 * 바퀴를 돌리는 모터 ESP32 다. 값이 하나뿐이던 동안, 2026-08-06 실기동에서 모터
 * 보드만 죽었을 때 화면이 그것을 말할 방법이 없었다.
 *
 * 신선도는 `mcuTime` 으로 본다 — 같은 `robot_metrics` 행에서 오므로 시각도 같다.
 * `false` 와 `null` 을 구별하는 것이 요점이다: `false` 는 「확인했고 끊김」이라
 * 경고이고, `null` 은 「확인할 수단이 없음」(모터 보드 없는 구성·옛 젯슨)이라
 * 경고가 아니다. 화면이 `null` 에 경고하면 모터 없는 개발 구성이 상시 빨간불이 된다.
 */
export function motorLinkFromLatest(d: TelemetryLatest, now: number): boolean | null {
  const mcuTime = d.mcuTime == null ? null : Date.parse(d.mcuTime);
  const fresh = mcuTime !== null && now - mcuTime <= SENSOR_FRESH_MS;
  return fresh ? d.motorLinkOk ?? null : null;
}

/**
 * 최신값 → 제어 모드 (S15P11A301-350).
 *
 * **세 값을 그대로 지킨다.** `"MANUAL"` / `"AUTO"` / `null`(모름). `null` 을 AUTO 로
 * 뭉개면 안 된다 — 2026-08-08 실기동에서 관제가 14분간 「자율」을 보여준 것이
 * 정확히 그 형태였고, 이 함수는 그것을 고치려고 만들었다.
 *
 * 신선도도 `mcuTime` 으로 본다. 이 값의 출처는 `robots` 라 엄밀히는 다른 행이지만,
 * 둘 다 젯슨의 1Hz 채널이 갱신하므로 MCU 가 신선하면 이 값도 신선하다. `robots` 에는
 * 쓸 만한 시각이 없다 — `last_seen_at` 은 PRESENCE 메시지만 갱신해서, 로봇이 접속만
 * 유지한 채 죽어도 「방금 갱신된 MANUAL」로 보인다.
 *
 * 모르는 문자열은 `null` 로 떨어뜨린다. 옛 백엔드가 키를 안 보내면 `undefined` 이고
 * 그것도 `null` 이다(`motionFromLatest` 가 겪은 그 문제다).
 */
export function controlModeFromLatest(
  d: TelemetryLatest,
  now: number,
): "MANUAL" | "AUTO" | null {
  const mcuTime = d.mcuTime == null ? null : Date.parse(d.mcuTime);
  const fresh = mcuTime !== null && now - mcuTime <= SENSOR_FRESH_MS;
  if (!fresh) return null;
  return d.controlMode === "MANUAL" || d.controlMode === "AUTO" ? d.controlMode : null;
}

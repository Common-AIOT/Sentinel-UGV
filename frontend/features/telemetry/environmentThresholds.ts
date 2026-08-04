/**
 * DHT11 환경값 판정 (S15P11A301-214).
 *
 * 명세에 온습도 임계값 규정이 없다 — 04장 22.2·14.5 는 "경고만 발생", "보조 센서"
 * 까지만 정하고 숫자를 두지 않았다. 그래서 여기서 정하고 근거를 남긴다.
 *
 * ## 센서 범위가 판정보다 먼저다
 *
 * DHT11 데이터시트: **습도 20~90%RH(±5%), 온도 0~50°C(±2°C)**, 해상도 각 1.
 *
 * 임계값만 두면 **범위 밖이 경고가 아니라 침묵이 된다.** 습도가 실제로 95% 여도
 * DHT11 은 90 근처를 보고하거나 읽기에 실패하고, 화면은 "90.0%" 를 정상 측정값처럼
 * 보여준다. 보는 사람은 그것이 상한이라는 사실을 모른다 — 이 프로젝트에서 반복해서
 * 물린 "없는 것을 있다고 믿게 하는" 형태다.
 *
 * 그래서 판정을 두 단계로 둔다.
 *
 * 1. **포화(saturated)** — 값이 센서 유효 범위의 끝에 붙었다. 실제는 더 극단일 수
 *    있으므로 `≥90` 처럼 부등호로 표시한다.
 * 2. **경고(warn)** — 범위 안에서 위험 수준이다.
 *
 * ## 경고 임계값과 근거
 *
 * | 항목 | 값 | 근거 |
 * |---|---|---|
 * | 습도 상한 | 80% | 데이터시트 상한 90 에서 오차 ±5 와 포화 여유를 뺀 값. 85 로 두면 경고가 뜨는 구간(85~90)이 오차 폭과 같아 실측이 그 구간에 있는지 판단할 수 없다 |
 * | 온도 상한 | 40°C | 상한 50 에서 여유 10 (오차 ±2 의 5배). 화재 근처를 알리는 목적 |
 * | 온도 하한 | 5°C | 하한 0 에서 여유 5 (오차의 2.5배) |
 *
 * 습도만 85 → 80 으로 낮췄다. 온도 임계는 범위 여유가 충분해 그대로 둔다.
 *
 * ## 왜 순수 함수인가
 *
 * 숫자 판정에 DOM 이 없어야 시험할 수 있다. 임계값은 실측·시연 조건에 따라 바뀔
 * 값이라, 바뀔 때 시험이 함께 움직이는 것이 중요하다.
 */

/** DHT11 유효 측정 범위 (데이터시트). */
export const DHT11_TEMP_MIN_C = 0;
export const DHT11_TEMP_MAX_C = 50;
export const DHT11_HUMIDITY_MIN_PCT = 20;
export const DHT11_HUMIDITY_MAX_PCT = 90;

/** 포화 판정 여유. 값이 범위 끝에서 이 안쪽이면 "더 극단일 수 있다"로 본다. */
export const TEMP_SATURATION_MARGIN_C = 2; // 데이터시트 오차와 같은 크기
export const HUMIDITY_SATURATION_MARGIN_PCT = 2;

/** 경고 임계값. 근거는 파일 머리의 표. */
export const TEMP_WARN_HIGH_C = 40;
export const TEMP_WARN_LOW_C = 5;
export const HUMIDITY_WARN_HIGH_PCT = 80;

export type Saturation = "none" | "low" | "high";

export interface Reading {
  /** 화면에 그대로 넣는 문자열. 결측은 `—`, 포화는 `≥90` 처럼 부등호가 붙는다. */
  text: string;
  warn: boolean;
  saturation: Saturation;
}

function saturationOf(
  value: number, min: number, max: number, margin: number,
): Saturation {
  if (value >= max - margin) return "high";
  if (value <= min + margin) return "low";
  return "none";
}

function format(value: number, saturation: Saturation, min: number, max: number): string {
  // 포화면 부등호로 "이 값이 상·하한이다"를 드러낸다. 소수 한 자리는 버린다 —
  // 89.7 을 `≥89.7` 로 적으면 그 정밀도가 의미 있는 것처럼 보인다.
  if (saturation === "high") return `≥${max}`;
  if (saturation === "low") return `≤${min}`;
  return value.toFixed(1);
}

export function readTemperature(value: number | null): Reading {
  if (value === null) return { text: "—", warn: false, saturation: "none" };
  const saturation = saturationOf(
    value, DHT11_TEMP_MIN_C, DHT11_TEMP_MAX_C, TEMP_SATURATION_MARGIN_C,
  );
  return {
    text: format(value, saturation, DHT11_TEMP_MIN_C, DHT11_TEMP_MAX_C),
    // 포화는 그 자체로 경고다 — 범위를 벗어났다는 것은 임계값보다 나쁜 상황이다.
    //
    // 온도에서는 이 절이 **현재 임계값 아래서는 중복**이다(포화 48°C > 경고 40°C,
    // 포화 2°C < 경고 5°C). 그래도 둔다 — 습도 하한에서는 중복이 아니고(포화 22%
    // 는 경고 80% 밖이다), 임계값이 바뀌면 온도에서도 필요해진다. 그 관계를
    // 시험이 지킨다(`포화 구간이 경고 구간 안에 있다`).
    warn: saturation !== "none" || value > TEMP_WARN_HIGH_C || value < TEMP_WARN_LOW_C,
    saturation,
  };
}

export function readHumidity(value: number | null): Reading {
  if (value === null) return { text: "—", warn: false, saturation: "none" };
  const saturation = saturationOf(
    value, DHT11_HUMIDITY_MIN_PCT, DHT11_HUMIDITY_MAX_PCT, HUMIDITY_SATURATION_MARGIN_PCT,
  );
  return {
    text: format(value, saturation, DHT11_HUMIDITY_MIN_PCT, DHT11_HUMIDITY_MAX_PCT),
    warn: saturation !== "none" || value > HUMIDITY_WARN_HIGH_PCT,
    saturation,
  };
}

/**
 * 포화 상태를 한 줄로 설명한다. 없으면 null.
 *
 * 부등호만으로는 "왜 부등호인가"를 모른다. 센서 한계라는 사실을 적어야 보는 사람이
 * 배선 문제로 오해하지 않는다.
 */
export function saturationNote(temp: Reading, humidity: Reading): string | null {
  const parts: string[] = [];
  if (temp.saturation === "high") parts.push(`온도 ${DHT11_TEMP_MAX_C}°C`);
  if (temp.saturation === "low") parts.push(`온도 ${DHT11_TEMP_MIN_C}°C`);
  if (humidity.saturation === "high") parts.push(`습도 ${DHT11_HUMIDITY_MAX_PCT}%`);
  if (humidity.saturation === "low") parts.push(`습도 ${DHT11_HUMIDITY_MIN_PCT}%`);
  if (parts.length === 0) return null;
  return `DHT11 측정 한계(${parts.join(" · ")}) — 실제는 더 극단일 수 있습니다`;
}

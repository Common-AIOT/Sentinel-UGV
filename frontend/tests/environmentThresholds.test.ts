/**
 * DHT11 환경값 판정 시험 (S15P11A301-214).
 *
 * 임계값은 실측·시연 조건에 따라 바뀔 값이다. 여기 숫자를 박아 두어, 바꿀 때
 * **의도적으로** 시험을 함께 고치게 만든다 — 조용히 바뀌면 "습도 경고가 왜 안
 * 뜨나"를 나중에 조사한다.
 *
 * 핵심은 포화 처리다. 센서 범위 밖에서 경고가 침묵하면 없는 안전장치를 있다고
 * 믿게 된다.
 */

import { describe, expect, it } from "vitest";
import {
  HUMIDITY_SATURATION_MARGIN_PCT,
  TEMP_SATURATION_MARGIN_C,
  DHT11_HUMIDITY_MAX_PCT,
  DHT11_HUMIDITY_MIN_PCT,
  DHT11_TEMP_MAX_C,
  DHT11_TEMP_MIN_C,
  HUMIDITY_WARN_HIGH_PCT,
  TEMP_WARN_HIGH_C,
  TEMP_WARN_LOW_C,
  readHumidity,
  readTemperature,
  saturationNote,
} from "@/features/telemetry/environmentThresholds";

describe("DHT11 범위 상수는 데이터시트 값이다", () => {
  it("습도 20~90%, 온도 0~50°C", () => {
    // 판정 전체가 이 네 값에 기대므로 리터럴로 박는다. 센서를 바꾸면 여기가
    // 먼저 깨져야 한다.
    expect(DHT11_HUMIDITY_MIN_PCT).toBe(20);
    expect(DHT11_HUMIDITY_MAX_PCT).toBe(90);
    expect(DHT11_TEMP_MIN_C).toBe(0);
    expect(DHT11_TEMP_MAX_C).toBe(50);
  });

  it("경고 임계는 범위 안이다", () => {
    // 임계가 범위 밖이면 그 경고는 영원히 발동하지 않는다.
    expect(TEMP_WARN_HIGH_C).toBeLessThan(DHT11_TEMP_MAX_C);
    expect(TEMP_WARN_LOW_C).toBeGreaterThan(DHT11_TEMP_MIN_C);
    expect(HUMIDITY_WARN_HIGH_PCT).toBeLessThan(DHT11_HUMIDITY_MAX_PCT);
  });

  it("포화 구간이 경고 구간 안에 있다 (온도)", () => {
    // `readTemperature` 의 `saturation !== "none"` 절이 온도에서 중복인 근거다.
    // 임계값을 바꿔 이 관계가 깨지면(예: 경고 상한을 49 로 올리면) 그 절이
    // 실제로 필요해지므로, 여기서 먼저 알려 준다.
    expect(DHT11_TEMP_MAX_C - TEMP_SATURATION_MARGIN_C).toBeGreaterThan(TEMP_WARN_HIGH_C);
    expect(DHT11_TEMP_MIN_C + TEMP_SATURATION_MARGIN_C).toBeLessThan(TEMP_WARN_LOW_C);
  });

  it("습도 하한 포화는 경고 구간 밖이다 — 포화 절이 반드시 필요하다", () => {
    // 습도 경고는 상한만 본다(>80). 하한 포화(≤22%)는 그 조건에 걸리지 않으므로
    // 포화 절 없이는 **건조 극단이 조용히 지나간다.**
    expect(DHT11_HUMIDITY_MIN_PCT + HUMIDITY_SATURATION_MARGIN_PCT)
      .toBeLessThan(HUMIDITY_WARN_HIGH_PCT);
    expect(readHumidity(DHT11_HUMIDITY_MIN_PCT + 1).warn).toBe(true);
  });

  it("습도 임계가 오차 폭만큼 상한에서 떨어져 있다", () => {
    // 85 였을 때의 문제 — 경고 구간(85~90)이 데이터시트 오차(±5)와 같아서
    // 실측이 그 구간에 있는지 판단할 수 없었다. 최소 2배 여유를 요구한다.
    expect(DHT11_HUMIDITY_MAX_PCT - HUMIDITY_WARN_HIGH_PCT).toBeGreaterThanOrEqual(10);
  });
});

describe("readTemperature", () => {
  it("결측은 — 이고 경고가 아니다", () => {
    const r = readTemperature(null);
    expect(r.text).toBe("—");
    expect(r.warn).toBe(false);
    expect(r.saturation).toBe("none");
  });

  it("정상 범위는 소수 한 자리", () => {
    const r = readTemperature(24.3);
    expect(r.text).toBe("24.3");
    expect(r.warn).toBe(false);
  });

  it("실측 조건(24.3°C)에서 경고가 뜨지 않는다", () => {
    // 2026-08-04 실측값. 평상 실내에서 경고가 뜨면 경고가 무의미해진다.
    expect(readTemperature(24.3).warn).toBe(false);
  });

  it("고온 경고", () => {
    expect(readTemperature(41).warn).toBe(true);
    expect(readTemperature(40).warn).toBe(false); // 초과여야 한다
  });

  it("저온 경고", () => {
    expect(readTemperature(4.9).warn).toBe(true);
    expect(readTemperature(5).warn).toBe(false);
  });

  it("상한 포화는 부등호로 표시하고 경고다", () => {
    // 48 부터 포화(50 − 오차 2). 화면에 "48.0" 을 정상 측정처럼 보여주면
    // 그것이 센서 한계라는 사실이 사라진다.
    const r = readTemperature(48.5);
    expect(r.saturation).toBe("high");
    expect(r.text).toBe("≥50");
    expect(r.warn).toBe(true);
  });

  it("하한 포화", () => {
    const r = readTemperature(1.0);
    expect(r.saturation).toBe("low");
    expect(r.text).toBe("≤0");
    expect(r.warn).toBe(true);
  });

  it("범위 밖 값도 포화로 처리한다", () => {
    // 센서가 범위 밖을 보고할 수 있다(오차·펌웨어). 그때 부등호 없이 그대로
    // 보여주면 신뢰할 수 없는 값이 측정값처럼 보인다.
    expect(readTemperature(60).text).toBe("≥50");
    expect(readTemperature(-5).text).toBe("≤0");
  });
});

describe("readHumidity", () => {
  it("결측은 —", () => {
    expect(readHumidity(null).text).toBe("—");
  });

  it("실측 조건(61.8%)에서 경고가 뜨지 않는다", () => {
    expect(readHumidity(61.8).warn).toBe(false);
    expect(readHumidity(61.8).text).toBe("61.8");
  });

  it("80% 초과에서 경고", () => {
    expect(readHumidity(80).warn).toBe(false);
    expect(readHumidity(80.1).warn).toBe(true);
  });

  it("85% 는 이제 경고다 (임계를 80 으로 낮췄다)", () => {
    // 이전 임계가 85 였으므로 85 는 경고가 아니었다. 그 회귀를 막는다.
    expect(readHumidity(85).warn).toBe(true);
  });

  it("상한 포화 — 재난 현장의 고습을 센서가 못 읽는 구간", () => {
    const r = readHumidity(89);
    expect(r.saturation).toBe("high");
    expect(r.text).toBe("≥90");
    expect(r.warn).toBe(true);
  });

  it("하한 포화", () => {
    const r = readHumidity(21);
    expect(r.saturation).toBe("low");
    expect(r.text).toBe("≤20");
    expect(r.warn).toBe(true);
  });

  it("0% 는 결측이 아니라 포화다", () => {
    // 젯슨 계약: null 은 모름, 0 은 값이다. 0 을 — 로 보여주면 두 사실이 섞인다.
    const r = readHumidity(0);
    expect(r.text).not.toBe("—");
    expect(r.saturation).toBe("low");
  });
});

describe("saturationNote", () => {
  it("포화가 없으면 null", () => {
    expect(saturationNote(readTemperature(24), readHumidity(60))).toBeNull();
  });

  it("센서 한계라는 사실을 적는다", () => {
    // 부등호만 보면 배선 문제로 오해한다.
    const note = saturationNote(readTemperature(24), readHumidity(89));
    expect(note).toContain("DHT11");
    expect(note).toContain("90");
    expect(note).toMatch(/더 극단/);
  });

  it("둘 다 포화면 둘 다 적는다", () => {
    const note = saturationNote(readTemperature(49), readHumidity(89));
    expect(note).toContain("온도");
    expect(note).toContain("습도");
  });

  it("결측은 포화가 아니다", () => {
    expect(saturationNote(readTemperature(null), readHumidity(null))).toBeNull();
  });
});

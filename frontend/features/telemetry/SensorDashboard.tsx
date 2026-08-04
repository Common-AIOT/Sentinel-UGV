"use client";

import { Thermometer, Droplets } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import {
  readHumidity,
  readTemperature,
  saturationNote,
} from "./environmentThresholds";

/**
 * 환경 센서 패널 — DHT11 실측(#205). 임무 중 텔레메트리 최신 버킷을 폴링한다.
 *
 * null 은 "모름"이고 0 은 값이다(젯슨 계약) — 값이 없으면 —(결측)로 보여주고,
 * MCU 연결 상태로 "보드가 빠졌나(연결 문제)"와 "온습도만 비었나(센서 문제)"를
 * 가른다. 그럴싸한 난수를 보여주던 목업은 뺐다.
 *
 * 배터리를 뺐다 (S15P11A301-200). 전압 계측이 없어(#174) telemetry battery 가
 * 항상 null 이다. 게이지를 두면 없는 값을 있는 것처럼 보여준다. 명세 23.4의
 * "배터리 20% 이하 탐사 종료"는 로봇이 판단하고 화면은 결과를 받는다.
 *
 * 속도(`linearVelocity`·`angularVelocity`)도 넣지 않는다 (S15P11A301-214에서
 * 확정). 값은 이제 실측으로 오지만 **관제자의 행동을 바꾸지 않는다** — 로봇이
 * 움직이는지는 실시간 지도의 화살표로 보이고(#227), 속도 상한은 로봇이 강제하며
 * (24.2) 관제자에게 조절 수단이 없고, 비정상 감속·정지는 임무 상태로 드러난다.
 * 배터리·방위각을 뺀 것과 같은 기준이다. DB·임무 이력 그래프에는 남긴다 —
 * 사후 주행 품질 분석(S15P11A301-248)에 실제로 쓰는 값이다.
 *
 * 임계값 판정과 센서 범위 처리는 `environmentThresholds.ts` 다 — DOM 없이
 * 시험할 수 있어야 임계값이 바뀔 때 시험이 함께 움직인다.
 */

interface CompactSensorProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  warn?: boolean;
}

/**
 * 소형 센서 타일. 게이지를 두지 않는다. pct가 (온도/80)처럼 임의 기준이면
 * 막대가 알려주는 것이 없는데 카드마다 세로를 먹는다.
 */
function CompactSensor({ icon, label, value, unit, warn }: CompactSensorProps) {
  return (
    <div className={`border rounded px-2.5 py-2 ${warn ? "border-accent/30 bg-accent/5" : "border-border bg-secondary/20"}`}>
      <div className="flex items-center gap-1.5 mb-1">
        <span className={warn ? "text-accent" : "text-muted-foreground"}>{icon}</span>
        <span className="text-[11px] text-muted-foreground">{label}</span>
        {warn && <span className="font-mono text-[11px] text-accent ml-auto">⚠</span>}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={`font-mono text-base font-semibold tabular-nums ${warn ? "text-accent" : "text-foreground"}`}>
          {value}
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">{unit}</span>
      </div>
    </div>
  );
}

export default function SensorDashboard() {
  const { sensors } = useRobot();
  const temp = readTemperature(sensors.temperature);
  const humidity = readHumidity(sensors.humidity);
  const noReading = sensors.temperature === null && sensors.humidity === null;

  // 결측의 이유를 한 줄로 — 값이 없을 때 화면이 침묵하면 고장인지 대기인지 알 수 없다.
  // 대기 중에도 최신값(/telemetry/latest)을 폴링하므로(#255) 임무 여부는 따지지 않는다.
  const note =
    sensors.mcuConnected === false ? "센서 보드(MCU) 연결 끊김"
    : noReading && sensors.mcuConnected === true ? "보드 연결됨 · 센서 응답 없음"
    : noReading ? "측정값 수신 대기 중"
    // 포화는 결측이 아니라 "센서 한계"다. 부등호만 보면 배선 문제로 오해한다.
    : saturationNote(temp, humidity);

  return (
    <div className="p-3.5 space-y-2.5">
      <span className="text-[11px] font-medium text-muted-foreground">센서</span>
      <div className="grid grid-cols-2 gap-2">
        <CompactSensor
          icon={<Thermometer size={12} />}
          label="온도"
          value={temp.text}
          unit="°C"
          warn={temp.warn}
        />
        <CompactSensor
          icon={<Droplets size={12} />}
          label="습도"
          value={humidity.text}
          unit="%"
          warn={humidity.warn}
        />
      </div>
      {note && (
        <p className={`text-[11px] ${sensors.mcuConnected === false ? "text-accent" : "text-muted-foreground"}`}>
          {note}
        </p>
      )}
    </div>
  );
}

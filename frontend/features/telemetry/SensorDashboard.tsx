"use client";

import { Thermometer, Droplets } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";

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
  const { sensors, missionId } = useRobot();
  const tempWarn = sensors.temperature !== null && (sensors.temperature > 40 || sensors.temperature < 5);
  const humWarn = sensors.humidity !== null && sensors.humidity > 85;
  const noReading = sensors.temperature === null && sensors.humidity === null;

  // 결측의 이유를 한 줄로 — 값이 없을 때 화면이 침묵하면 고장인지 대기인지 알 수 없다.
  const note =
    !missionId ? "대기 중 — 임무 중에 실측값이 표시됩니다"
    : sensors.mcuConnected === false ? "센서 보드(MCU) 연결 끊김"
    : noReading && sensors.mcuConnected === true ? "보드 연결됨 · 센서 응답 없음"
    : noReading ? "측정값 수신 대기 중"
    : null;

  return (
    <div className="p-3.5 space-y-2.5">
      <span className="text-[11px] font-medium text-muted-foreground">센서</span>
      <div className="grid grid-cols-2 gap-2">
        <CompactSensor
          icon={<Thermometer size={12} />}
          label="온도"
          value={sensors.temperature === null ? "—" : sensors.temperature.toFixed(1)}
          unit="°C"
          warn={tempWarn}
        />
        <CompactSensor
          icon={<Droplets size={12} />}
          label="습도"
          value={sensors.humidity === null ? "—" : sensors.humidity.toFixed(1)}
          unit="%"
          warn={humWarn}
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

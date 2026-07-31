"use client";

import { Thermometer, Droplets } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 환경 센서 패널.
 *
 * 배터리를 뺐다 (S15P11A301-200). 잔량을 현실적으로 계측할 수단이 없다 —
 * telemetry의 battery는 ESP32 연동(S15P11A301-174) 의존이고 cloud_bridge가
 * null로 보내며, 조회 API의 battery도 항상 null이다. 게이지와 임계선을 두면
 * 없는 값을 있는 것처럼 보여준다.
 *
 * 명세 23.4의 "배터리 20% 이하 탐사 종료"는 그대로 유효하다. 그 판단은 로봇이
 * 하고 화면은 결과(종료 사유)를 받는다 — 화면에서 잔량을 안 보여주는 것과
 * 종료 조건이 없는 것은 다르다.
 *
 * 온습도는 DHT11 실측이 들어올 예정이다(ESP32 연동 커밋 대기). 그때까지 값은
 * 목업이고, 배선은 cloud_bridge의 environment=None을 채우는 것부터다.
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
  const tempWarn = sensors.temperature > 40 || sensors.temperature < 5;
  const humWarn = sensors.humidity > 85;

  return (
    <div className="p-3.5 space-y-2.5">
      <span className="text-[11px] font-medium text-muted-foreground">센서</span>
      <div className="grid grid-cols-2 gap-2">
        <CompactSensor
          icon={<Thermometer size={12} />}
          label="온도"
          value={sensors.temperature.toFixed(1)}
          unit="°C"
          warn={tempWarn}
        />
        <CompactSensor
          icon={<Droplets size={12} />}
          label="습도"
          value={sensors.humidity.toFixed(1)}
          unit="%"
          warn={humWarn}
        />
      </div>
    </div>
  );
}

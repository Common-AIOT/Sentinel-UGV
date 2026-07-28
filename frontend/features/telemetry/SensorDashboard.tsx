"use client";

import { Thermometer, Droplets, Battery } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import { BATTERY_ABORT_PCT } from "@/features/robot/mockData";

interface SensorCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  pct?: number;
  warn?: boolean;
  color?: string;
  /** 이 값을 넘거나 밑돌면 임무가 종료되는 선. 게이지에 눈금으로 표시한다. */
  threshold?: { pct: number; label: string };
}

function SensorCard({ icon, label, value, unit, pct, warn, color = "#45c98c", threshold }: SensorCardProps) {
  return (
    <div className={`border rounded p-3 bg-secondary/20 ${warn ? "border-accent/30 bg-accent/5" : "border-border"}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span className={warn ? "text-accent" : "text-muted-foreground"}>{icon}</span>
          <span className="text-[11px] text-muted-foreground">{label}</span>
        </div>
        {warn && <span className="font-mono text-xs text-accent">⚠</span>}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={`font-mono text-lg font-semibold ${warn ? "text-accent" : "text-foreground"}`}>{value}</span>
        <span className="font-mono text-xs text-muted-foreground">{unit}</span>
      </div>
      {pct !== undefined && (
        <>
          <div className="mt-2.5 h-1.5 bg-muted rounded-full relative">
            <div
              className="h-full rounded-full transition-all duration-1000"
              style={{ width: `${pct}%`, backgroundColor: warn ? "var(--accent)" : color }}
            />
            {threshold && (
              <span
                className="absolute -top-0.5 h-2.5 w-0.5 bg-destructive/80 rounded-full"
                style={{ left: `${threshold.pct}%` }}
                title={threshold.label}
              />
            )}
          </div>
          {threshold && (
            <p className="font-mono text-[10px] text-muted-foreground mt-1">{threshold.label}</p>
          )}
        </>
      )}
    </div>
  );
}

/**
 * 온도·습도용 소형 타일. 게이지를 뺐다. pct가 (온도/80)처럼 임의 기준이라
 * 막대가 알려주는 것이 없는데 카드마다 세로 24px씩 먹고, 그만큼 배터리가
 * 접힘 아래로 밀린다. 임계선이 실제로 있는 배터리만 게이지를 유지한다.
 */
function CompactSensor({
  icon,
  label,
  value,
  unit,
  warn,
}: Pick<SensorCardProps, "icon" | "label" | "value" | "unit" | "warn">) {
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
  // 명세 23.4: 배터리 20% 이하는 탐사 종료 조건이다. 임계값을 화면에서
  // 따로 정의하면 로봇이 복귀를 시작하는 시점과 경고 시점이 어긋난다.
  const batWarn = sensors.battery <= BATTERY_ABORT_PCT;

  return (
    <div className="p-3.5 space-y-2.5">
      <span className="text-[11px] font-medium text-muted-foreground">센서</span>
      {/* 온습도는 2열로 붙이고 배터리만 전폭으로 둔다. 배터리가 탐사 종료
          조건이라 세 개 중 유일하게 접힘 위에 있어야 하는 값이다. */}
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
      <div className="grid grid-cols-1">
        <SensorCard
          icon={<Battery size={12} />}
          label="배터리"
          value={sensors.battery.toFixed(0)}
          unit="%"
          pct={sensors.battery}
          warn={batWarn}
          color={sensors.battery > 50 ? "#45c98c" : sensors.battery > BATTERY_ABORT_PCT ? "#e2a542" : "#e5534b"}
          threshold={{ pct: BATTERY_ABORT_PCT, label: `${BATTERY_ABORT_PCT}% 이하 자동 복귀` }}
        />
      </div>
    </div>
  );
}

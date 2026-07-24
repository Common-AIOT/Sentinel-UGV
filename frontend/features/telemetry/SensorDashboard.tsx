"use client";

import { Thermometer, Droplets, Battery } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";

interface SensorCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  pct?: number;
  warn?: boolean;
  color?: string;
}

function SensorCard({ icon, label, value, unit, pct, warn, color = "#45c98c" }: SensorCardProps) {
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
        <div className="mt-2.5 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-1000"
            style={{ width: `${pct}%`, backgroundColor: warn ? "var(--accent)" : color }}
          />
        </div>
      )}
    </div>
  );
}

export default function SensorDashboard() {
  const { sensors } = useRobot();
  const tempWarn = sensors.temperature > 40 || sensors.temperature < 5;
  const humWarn = sensors.humidity > 85;
  const batWarn = sensors.battery < 20;

  return (
    <div className="p-3.5 space-y-2.5">
      <span className="text-[11px] font-medium text-muted-foreground">센서</span>
      <div className="grid grid-cols-1 gap-2">
        <SensorCard
          icon={<Thermometer size={12} />}
          label="온도"
          value={sensors.temperature.toFixed(1)}
          unit="°C"
          pct={(sensors.temperature / 80) * 100}
          warn={tempWarn}
        />
        <SensorCard
          icon={<Droplets size={12} />}
          label="습도"
          value={sensors.humidity.toFixed(1)}
          unit="%"
          pct={sensors.humidity}
          warn={humWarn}
          color="#58a6ff"
        />
        <SensorCard
          icon={<Battery size={12} />}
          label="배터리"
          value={sensors.battery.toFixed(0)}
          unit="%"
          pct={sensors.battery}
          warn={batWarn}
          color={sensors.battery > 50 ? "#45c98c" : sensors.battery > 20 ? "#e2a542" : "#e5534b"}
        />
      </div>
    </div>
  );
}

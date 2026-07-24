"use client";

import { Navigation } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import { toast } from "sonner";

const MODE_LABEL: Record<string, string> = {
  EXPLORE: "탐색",
  RETURN: "복귀",
  IDLE: "대기",
  MANUAL: "수동",
};

const MODE_COLOR: Record<string, string> = {
  EXPLORE: "text-primary",
  RETURN: "text-accent",
  IDLE: "text-muted-foreground",
  MANUAL: "text-blue-400",
};

const MODE_BG: Record<string, string> = {
  EXPLORE: "bg-primary/10 border-primary/30",
  RETURN: "bg-accent/10 border-accent/30",
  IDLE: "bg-muted border-border",
  MANUAL: "bg-blue-500/10 border-blue-500/30",
};

function formatUptime(seconds: number) {
  const h = Math.floor(seconds / 3600).toString().padStart(2, "0");
  const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, "0");
  const s = (seconds % 60).toString().padStart(2, "0");
  return `${h}:${m}:${s}`;
}

export default function StatusPanel() {
  const { status, wsConnected, sendCommand } = useRobot();

  const handleCommand = async (type: string, label: string) => {
    try {
      await sendCommand(type);
      toast.success(`명령 전송: ${label}`, {
        description: `로봇 모드 → ${MODE_LABEL[type.toUpperCase()] ?? type}`,
        duration: 3000,
      });
    } catch {
      toast.error("명령 실패", { description: "연결 상태를 확인하세요" });
    }
  };

return (
    <div className="p-3.5 space-y-3 border-b border-border">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-muted-foreground">로봇 상태</span>
        <div className="flex items-center gap-1.5">
          <div className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-primary animate-pulse" : "bg-destructive"}`} />
          <span className="text-[11px] text-muted-foreground">{wsConnected ? "연결됨" : "오프라인"}</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className={`border rounded px-2.5 py-2 ${MODE_BG[status.mode]}`}>
          <p className="text-[10px] text-muted-foreground mb-0.5">모드</p>
          <p className={`text-sm font-semibold ${MODE_COLOR[status.mode]}`}>{MODE_LABEL[status.mode]}</p>
        </div>
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
          <p className="text-[10px] text-muted-foreground mb-0.5">속도</p>
          <p className="font-mono text-sm font-semibold text-foreground">
            {status.speed.toFixed(2)} <span className="text-[10px] text-muted-foreground font-normal">m/s</span>
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
          <p className="text-[10px] text-muted-foreground mb-0.5">방위각</p>
          <p className="font-mono text-sm text-foreground">{status.heading}°</p>
        </div>
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
          <p className="text-[10px] text-muted-foreground mb-0.5">가동시간</p>
          <p className="font-mono text-sm text-foreground">{formatUptime(status.uptime)}</p>
        </div>
      </div>

      <div className="space-y-1.5">
        <button
          onClick={() => handleCommand("explore", "탐색 시작")}
          className="w-full text-xs font-medium px-3 py-2 rounded border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 transition-colors flex items-center justify-center gap-2"
        >
          <Navigation size={13} />
          탐색 시작
        </button>
        <button
          onClick={() => handleCommand("return", "베이스캠프 복귀")}
          className="w-full text-xs font-medium px-3 py-2 rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 transition-colors"
        >
          ⟵ 베이스캠프 복귀
        </button>
      </div>
    </div>
  );
}

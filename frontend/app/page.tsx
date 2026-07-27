"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Archive, Radio, User, Cpu, Gamepad2 } from "lucide-react";
import LidarMap from "@/features/mapping/LidarMap";
import VideoPanel from "@/features/streaming/VideoPanel";
import StatusPanel from "@/features/telemetry/StatusPanel";
import SensorDashboard from "@/features/telemetry/SensorDashboard";
import Joystick from "@/features/control/Joystick";
import { useRobot } from "@/features/robot/RobotContext";

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const active = pathname === to;
  return (
    <Link
      href={to}
      className={`flex items-center gap-1.5 text-xs font-medium border rounded px-2.5 py-1 transition-colors ${
        active
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:text-primary hover:border-primary/30"
      }`}
    >
      {children}
    </Link>
  );
}

function TopBar() {
  const { wsConnected, detections } = useRobot();
  // 시계는 클라이언트에서만 렌더한다. 서버 렌더와 클라이언트 렌더 사이에
  // 초가 흐르면 hydration 불일치 오류가 난다. 초기값을 null로 두고
  // 마운트 후에 채우면 서버는 시계를 그리지 않아 불일치가 생기지 않는다.
  const [clock, setClock] = useState<string | null>(null);
  useEffect(() => {
    const update = () => setClock(new Date().toLocaleTimeString());
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="h-10 flex-shrink-0 flex items-center justify-between px-4 border-b border-border bg-card/60 backdrop-blur z-10">
      {/* 왼쪽: 로고 + 페이지 링크 */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Radio size={13} className="text-primary" />
          <span className="font-mono text-sm text-primary font-semibold tracking-wider">GCS</span>
        </div>
        <div className="w-px h-4 bg-border" />
        <NavLink to="/detections">
          <User size={11} />
          탐지
          {detections.length > 0 && (
            <span className="bg-accent/80 text-accent-foreground font-mono text-[9px] px-1 rounded-full">
              {detections.length}
            </span>
          )}
        </NavLink>
        <NavLink to="/blackbox">
          <Archive size={11} />
          블랙박스
        </NavLink>
      </div>

      {/* 오른쪽: 연결 상태 + 시계 */}
      <div className="flex items-center gap-3">
        <div className={`w-2 h-2 rounded-full ${wsConnected ? "bg-primary animate-pulse" : "bg-destructive"}`} />
        <span className="font-mono text-xs text-muted-foreground tabular-nums">
          {clock ?? "--:--:--"} UTC+9
        </span>
      </div>
    </div>
  );
}

// 자율 / 수동 세그먼트 토글
function ModeToggle({ manualMode, onToggle }: { manualMode: boolean; onToggle: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <span className="text-[11px] font-medium text-muted-foreground">운행 모드</span>
      <div
        className="relative flex rounded-lg border border-border bg-background p-0.5 cursor-pointer"
        style={{ width: 180 }}
        onClick={onToggle}
      >
        {/* 슬라이딩 배경 */}
        <div
          className={`absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-md transition-all duration-200 ${
            manualMode
              ? "left-[calc(50%+1px)] bg-accent/20 border border-accent/40"
              : "left-0.5 bg-primary/15 border border-primary/30"
          }`}
        />
        {/* 자율 */}
        <div className={`relative flex-1 flex items-center justify-center gap-1.5 py-1.5 z-10 transition-colors duration-150 ${!manualMode ? "text-primary" : "text-muted-foreground"}`}>
          <Cpu size={12} />
          <span className="text-[11px] font-medium">자율 주행</span>
        </div>
        {/* 수동 */}
        <div className={`relative flex-1 flex items-center justify-center gap-1.5 py-1.5 z-10 transition-colors duration-150 ${manualMode ? "text-accent" : "text-muted-foreground"}`}>
          <Gamepad2 size={12} />
          <span className="text-[11px] font-medium">수동 조종</span>
        </div>
      </div>
      {manualMode && (
        <span className="text-[10px] text-accent">조이스틱 입력 활성</span>
      )}
    </div>
  );
}

function ActiveDetectionPopup() {
  const { activeDetection, dismissDetection } = useRobot();
  if (!activeDetection) return null;

  return (
    <div className="absolute bottom-4 left-4 z-20 max-w-xs border border-accent/40 bg-card/95 backdrop-blur rounded p-3 shadow-xl">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
          <span className="text-xs font-semibold text-accent tracking-wide">인명 탐지</span>
        </div>
        <button onClick={dismissDetection} className="text-muted-foreground hover:text-foreground font-mono text-sm">✕</button>
      </div>
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 rounded flex items-center justify-center flex-shrink-0 text-xl" style={{ backgroundColor: activeDetection.thumbnailColor }}>
          👤
        </div>
        <div className="space-y-1">
          <p className="font-mono text-xs text-foreground">{activeDetection.location}</p>
          <p className="font-mono text-[10px] text-muted-foreground">{new Date(activeDetection.timestamp).toLocaleTimeString()}</p>
          <div className="flex items-center gap-2">
            <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${
              activeDetection.confidence >= 0.9
                ? "text-destructive border-destructive/30 bg-destructive/10"
                : "text-accent border-accent/30 bg-accent/10"
            }`}>
              신뢰도 {Math.round(activeDetection.confidence * 100)}%
            </span>
            <Link href="/detections" className="font-mono text-[10px] text-primary/60 hover:text-primary underline" onClick={dismissDetection}>
              상세 →
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function SmallLidarSlot() {
  return (
    <div className="relative bg-[#12171f] border-b border-border flex-shrink-0 overflow-hidden" style={{ height: 180 }}>
      <LidarMap />
      <div className="absolute top-1.5 left-1.5">
        <span className="font-mono text-[9px] text-primary/50 bg-black/50 px-1 rounded">LiDAR</span>
      </div>
    </div>
  );
}

export default function GCSPage() {
  const [swapped, setSwapped] = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const { sendCommand } = useRobot();

  const handleToggleManual = () => {
    const next = !manualMode;
    setManualMode(next);
    sendCommand(next ? "manual" : "auto");
  };

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <TopBar />

      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 relative overflow-hidden flex flex-col">
          {swapped ? (
            <VideoPanel isMain onSwap={() => setSwapped(false)} />
          ) : (
            <>
              <LidarMap />
              <ActiveDetectionPopup />
            </>
          )}
        </div>

        <div className="flex flex-col border-l border-border bg-card flex-shrink-0 overflow-y-auto" style={{ width: 308 }}>
          {swapped ? (
            <SmallLidarSlot />
          ) : (
            <VideoPanel onSwap={() => setSwapped(true)} />
          )}
          <StatusPanel />
          <SensorDashboard />
        </div>
      </div>

      {/* 스트리밍 뷰일 때만 하단 바 표시 */}
      {swapped && (
        <div className="flex-shrink-0 border-t border-border bg-card flex items-center justify-center gap-12 px-8" style={{ height: 156 }}>
          <ModeToggle manualMode={manualMode} onToggle={handleToggleManual} />
          <div className="w-px self-stretch bg-border my-3" />
          <div className={`transition-all duration-300 ${manualMode ? "opacity-100 scale-100" : "opacity-30 scale-95 pointer-events-none"}`}>
            <Joystick />
          </div>
        </div>
      )}
    </div>
  );
}

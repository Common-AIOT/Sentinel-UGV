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
import { useGamepad, useOnGamepadConnect } from "@/features/control/GamepadContext";
import { useRobot } from "@/features/robot/RobotContext";
import { toast } from "sonner";

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

// 자율 / 수동 세그먼트 토글.
// 명세 26.3: 주행 중 수동 전환은 PAUSED를 자동 경유하고, 수동 종료는 항상
// PAUSED로 복귀한다. 이 2단 전이는 sendCommand가 처리하므로 버튼은 하나다.
function ModeToggle({ manualMode, onToggle }: { manualMode: boolean; onToggle: () => void }) {
  const { connected: padConnected } = useGamepad();
  return (
    <div className="flex items-center gap-3">
      <span className="text-[11px] font-medium text-muted-foreground flex-shrink-0">운행 모드</span>
      <button
        type="button"
        role="switch"
        aria-checked={manualMode}
        aria-label="운행 모드 — 자율 주행과 수동 조종 전환"
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
      </button>
      <span className={`text-[11px] ${manualMode ? "text-accent" : "text-muted-foreground/70"}`}>
        {manualMode
          ? "게임패드 입력 활성"
          : padConnected
            ? "수동 종료 시 일시정지로 전환"
            : "수동 조종에는 게임패드 연결이 필요합니다"}
      </span>
    </div>
  );
}

/**
 * 조종 바.
 *
 * 자율 주행이 기본이므로 자율일 때는 모드 전환만 남기고 접는다. 조이스틱을
 * 흐리게 띄워 두면 자율 상태에서 화면 세로를 156px 잡아먹으면서 아무 일도
 * 하지 않는다. 대신 수동일 때는 항상 펼쳐 두어야 한다 — 뷰 전환에 묶으면
 * 수동 주행 중 조이스틱이 사라진다.
 */
function ControlBar({ manualMode, onToggle }: { manualMode: boolean; onToggle: () => void }) {
  const { connected } = useGamepad();
  // 게임패드가 붙어 있으면 연결 상태를 보여줘야 하므로 자율에서도 펼친다.
  const expanded = manualMode || connected;
  return (
    <div
      className="flex-shrink-0 border-t border-border bg-card flex items-center gap-8 px-6 transition-all duration-200"
      style={{ height: expanded ? 140 : 52 }}
    >
      <ModeToggle manualMode={manualMode} onToggle={onToggle} />
      {expanded && (
        <>
          <div className="w-px self-stretch bg-border my-4" />
          <Joystick />
        </>
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
    <div className="relative bg-[#12171f] border-b border-border flex-shrink-0 overflow-hidden" style={{ height: 148 }}>
      <LidarMap compact />
      <div className="absolute top-1.5 left-1.5">
        <span className="font-mono text-[11px] text-primary/80 bg-black/60 px-1.5 py-0.5 rounded">LiDAR</span>
      </div>
    </div>
  );
}

type MainView = "video" | "map";

export default function GCSPage() {
  // 영상이 기본 메인이다. 2D 점유 격자는 자율주행용 SLAM 산출물이라 관제자가
  // 상황을 파악하는 데는 영상이 낫고(명세 1.1의 임무 절정도 영상·음성 사건이다),
  // 지도는 위치 확인과 Frontier 실패 시 목표 지정에 쓰는 참조 화면이다.
  const [mainView, setMainView] = useState<MainView>("video");
  const { sendCommand, status } = useRobot();
  const { connected: padConnected } = useGamepad();

  // 수동 여부의 단일 출처는 로봇 상태다. 화면이 로컬 boolean을 따로 들면
  // 로봇은 자율인데 UI만 수동으로 보이는 조합이 생긴다.
  const manualMode = status.controlMode === "MANUAL";

  const handleToggleManual = () => {
    if (manualMode) {
      sendCommand("auto");
      return;
    }
    // 게임패드 없이 수동으로 들어가면 조종 수단이 없는 상태로 로봇이 수동
    // 모드가 된다. 진입을 막고 무엇이 필요한지 알린다.
    if (!padConnected) {
      toast.error("게임패드가 연결되지 않았습니다", {
        description: "USB 게임패드를 연결한 뒤 다시 시도하세요. 수동 조종은 물리 게임패드로만 가능합니다.",
        duration: 5000,
      });
      return;
    }
    sendCommand("manual");
  };

  // 연결을 곧바로 모드 전환으로 해석하지 않는다. 명세 26.3의 "MANUAL 진입은
  // SAFE_IDLE 또는 PAUSED에서만" + 자동 재출발 금지 원칙 때문에, 주행 중 USB를
  // 꽂았다는 이유로 모드가 바뀌면 안 된다. 전환은 운영자가 확정한다.
  useOnGamepadConnect(() => {
    if (status.controlMode === "MANUAL") return;
    toast("게임패드 연결됨", {
      description: "수동 조종으로 전환할 수 있습니다.",
      action: { label: "수동 전환", onClick: () => sendCommand("manual") },
      duration: 8000,
    });
  });

  const videoMain = mainView === "video";

  return (
    <div className="h-screen w-full flex flex-col overflow-hidden bg-background text-foreground">
      <TopBar />

      <div className="flex-1 flex overflow-hidden">
        {/* 조종 바를 본문 열 안에 둔다. 화면 전체 폭에 걸치면 사이드바 아래까지
            깔려서 내용이 어디에도 정렬되지 않고, 사이드바 세로도 그만큼 깎인다. */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 relative overflow-hidden flex flex-col">
            {videoMain ? (
              <VideoPanel isMain onSwap={() => setMainView("map")} />
            ) : (
              <LidarMap />
            )}
            {/* 탐지 팝업은 어느 뷰에서도 떠야 한다. 지도 뷰에만 두면 기본 화면인
                영상 뷰에서 인명 탐지 알림이 아예 뜨지 않는다. */}
            <ActiveDetectionPopup />
          </div>
          <ControlBar manualMode={manualMode} onToggle={handleToggleManual} />
        </div>

        <div className="flex flex-col border-l border-border bg-card flex-shrink-0 overflow-y-auto" style={{ width: 308 }}>
          {videoMain ? (
            <SmallLidarSlot />
          ) : (
            <VideoPanel onSwap={() => setMainView("video")} />
          )}
          <StatusPanel />
          <SensorDashboard />
        </div>
      </div>
    </div>
  );
}

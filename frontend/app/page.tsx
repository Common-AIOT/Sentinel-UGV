"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Archive, Radio, User } from "lucide-react";
import LidarMap from "@/features/mapping/LidarMap";
import VideoPanel from "@/features/streaming/VideoPanel";
import StatusPanel from "@/features/telemetry/StatusPanel";
import SensorDashboard from "@/features/telemetry/SensorDashboard";
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

/**
 * 탐지 카운터.
 *
 * 팝업(ActiveDetectionPopup)을 대체한다 (S15P11A301-196). 팝업은 발견 순간의
 * 영상 — 가장 봐야 할 화면 — 을 가렸다. 대신 숫자가 오를 때 배지를 잠깐
 * 강조해서 주변시로 알아챌 수 있게 한다. 조용히 숫자만 바뀌면 발표 중에
 * 아무도 못 본다.
 *
 * 상세 확인은 임무 이력(/blackbox)에서 한다 — 발견 목록·위치·이벤트 영상이
 * 전부 그쪽에 있다.
 */
function DetectionBadge() {
  const { detections } = useRobot();
  const [flash, setFlash] = useState(false);
  // 첫 렌더의 0 → N (새로고침 복구)에는 강조하지 않도록 이전 값을 기억한다.
  const prev = useRef(0);

  useEffect(() => {
    if (detections.length > prev.current && prev.current > 0) {
      setFlash(true);
      const timer = setTimeout(() => setFlash(false), 2500);
      prev.current = detections.length;
      return () => clearTimeout(timer);
    }
    prev.current = detections.length;
  }, [detections.length]);

  return (
    <div
      className={`flex items-center gap-1.5 text-xs font-medium border rounded px-2.5 py-1 transition-colors ${
        flash
          ? "border-accent bg-accent/20 text-accent animate-pulse"
          : "border-border text-muted-foreground"
      }`}
    >
      <User size={11} />
      탐지
      <span
        className={`font-mono text-[9px] px-1 rounded-full ${
          detections.length > 0
            ? "bg-accent/80 text-accent-foreground"
            : "bg-muted text-muted-foreground"
        }`}
      >
        {detections.length}
      </span>
    </div>
  );
}

function TopBar() {
  const { wsConnected } = useRobot();
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
      {/* 왼쪽: 로고 + 탐지 카운터 + 페이지 링크 */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Radio size={13} className="text-primary" />
          <span className="font-mono text-sm text-primary font-semibold tracking-wider">GCS</span>
        </div>
        <div className="w-px h-4 bg-border" />
        <DetectionBadge />
        <NavLink to="/blackbox">
          <Archive size={11} />
          임무 이력
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
  //
  // 수동 조종 UI(모드 토글·조이스틱)는 뺐다 (S15P11A301-196). 젯슨이
  // cmd/drive를 구독하지 않아(S15P11A301-143 범위 외) 동작하지 않는 UI였다.
  // 조이스틱 연동(S15P11A301-39)이 들어오면 features/control과 함께 되살린다.
  const [mainView, setMainView] = useState<MainView>("video");

  const videoMain = mainView === "video";

  return (
    <div className="h-screen w-full flex flex-col overflow-hidden bg-background text-foreground">
      <TopBar />

      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 relative overflow-hidden flex flex-col">
          {videoMain ? (
            <VideoPanel isMain onSwap={() => setMainView("map")} />
          ) : (
            <LidarMap />
          )}
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

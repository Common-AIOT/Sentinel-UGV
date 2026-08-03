"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Archive, Radio } from "lucide-react";
import LiveMap from "@/features/mapping/LiveMap";
import VideoPanel from "@/features/streaming/VideoPanel";
import StatusPanel from "@/features/telemetry/StatusPanel";
import SensorDashboard from "@/features/telemetry/SensorDashboard";
import ModeRow from "@/features/telemetry/ModeRow";
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
      {/* 왼쪽: 로고 + 페이지 링크 */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Radio size={13} className="text-primary" />
          <span className="font-mono text-sm text-primary font-semibold tracking-wider">GCS</span>
        </div>
        <div className="w-px h-4 bg-border" />
        {/* 탐지 수는 영상 오버레이로 옮겼다 (S15P11A301-200). 링크와 같은 모양의
            배지가 나란히 있어서 서로 다른 내용인 것이 읽히지 않았다. */}
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

/**
 * 미니맵 슬롯.
 *
 * 목업 격자를 **실시간 SLAM 지도로 바꿨다** (S15P11A301-227). 젯슨의
 * `foxglove_bridge`에 직접 붙어 `/map`·`/pose`를 받는다 — Foxglove Studio 가 보는
 * 것과 같은 데이터·같은 주기다.
 *
 * 그 전에는 "계약·발행·조회가 모두 없다"고 미뤄 두었는데 그 판단이 틀렸다. 경로가
 * 이미 있었고(bridge 가 /map 을 WebSocket 으로 내보내고 있었다), 막고 있다고 본 두
 * 가지(혼합 콘텐츠, 무인증 노출)는 bridge 의 `tls`·`capabilities`·`topic_whitelist`
 * 로 풀렸다(S15P11A301-224).
 *
 * 상태 줄은 영상 오버레이와 같은 형식(`OverlayLine`)을 쓴다 — 두 패널이 같은 종류의
 * 정보를 다르게 보여주지 않게 한다(S15P11A301-200).
 */
function MiniMapSlot() {
  return (
    <div className="relative bg-[#12171f] border-b border-border flex-shrink-0 overflow-hidden" style={{ height: 148 }}>
      <LiveMap />
    </div>
  );
}

type MainView = "video" | "map";

export default function GCSPage() {
  // 영상이 기본 메인이다. 2D 점유 격자는 자율주행용 SLAM 산출물이라 관제자가
  // 상황을 파악하는 데는 영상이 낫고(명세 1.1의 임무 절정도 영상·음성 사건이다),
  // 지도는 위치 확인과 Frontier 실패 시 목표 지정에 쓰는 참조 화면이다.
  //
  // 조이스틱과 하단 조종 바는 뺐다 (S15P11A301-196·197). 조종 수단이 모바일
  // 앱으로 정해져 관제 웹에 조종 입력이 없고, 하단 바가 세로 140px을 고정으로
  // 차지해 object-cover 영상이 잘렸다(720p가 깨져 보인 원인). 모드 전환만
  // 우측 패널 한 줄(ModeRow)로 남긴다 — 영상 높이를 건드리지 않는다.
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
            /* 메인도 같은 실시간 지도다. 예전에는 목업 격자(LidarMap)를 띄워서
               키우면 실시간 지도가 사라졌다(S15P11A301-227). */
            <LiveMap variant="full" />
          )}
        </div>

        <div className="flex flex-col border-l border-border bg-card flex-shrink-0 overflow-y-auto" style={{ width: 308 }}>
          {videoMain ? (
            <MiniMapSlot />
          ) : (
            <VideoPanel onSwap={() => setMainView("video")} />
          )}
          <ModeRow />
          <StatusPanel />
          <SensorDashboard />
        </div>
      </div>
    </div>
  );
}

"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Archive, Radio } from "lucide-react";
import LiveMap from "@/features/mapping/LiveMap";
import VideoPanel from "@/features/streaming/VideoPanel";
import StatusPanel from "@/features/telemetry/StatusPanel";
import SensorDashboard from "@/features/telemetry/SensorDashboard";
import MotionPanel from "@/features/telemetry/MotionPanel";
import CommandBar from "@/features/telemetry/CommandBar";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 왼쪽 세로 내비게이션 (S15P11A301-303).
 *
 * 임무 이력 링크를 상단 바에서 왼쪽 세로 바로 옮겼다. 상단 바에 있을 때는 로고
 * 옆에 링크 하나가 붙어 있어 「같은 화면의 일부」로 읽혔는데, 실제로는 **다른
 * 페이지로 나가는 유일한 출구**다. 세로 바에 두면 그 성격이 드러난다.
 *
 * 폭은 좁게 유지한다(56px). 페이지가 둘뿐이라 메뉴를 세로로 늘어놓을 것이 없고,
 * 넓히면 관제 화면이 그만큼 줄어든다.
 */
function SideNav() {
  const pathname = usePathname();
  const items = [
    { to: "/", label: "관제", icon: Radio },
    { to: "/blackbox", label: "이력", icon: Archive },
  ];
  return (
    <nav className="w-14 flex-shrink-0 flex flex-col items-center gap-1 py-2 border-r border-border bg-card">
      {items.map(({ to, label, icon: Icon }) => {
        const active = pathname === to;
        return (
          <Link
            key={to}
            href={to}
            className={`w-full flex flex-col items-center gap-1 py-2.5 border-l-2 transition-colors ${
              active
                ? "border-primary text-primary bg-primary/10"
                : "border-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Icon size={16} />
            <span className="text-[10px] font-medium">{label}</span>
          </Link>
        );
      })}
    </nav>
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
          <span className="font-mono text-sm text-primary font-semibold tracking-wider">SENTINEL-UGV</span>
        </div>
        {/* 임무 이력 링크는 왼쪽 세로 바로 옮겼다 (S15P11A301-303).
            탐지 수는 영상 오버레이로 옮겼다가 뺐다 (S15P11A301-200·300). */}
      </div>

      {/* 오른쪽: 관제 서버 연결 + 시계.
          연결 표시는 여기 하나만 둔다 (S15P11A301-303). 종전에는 사이드바에도
          같은 값이 있었다. 「서버」가 무엇인지 라벨과 툴팁으로 밝힌다 — 관제
          백엔드와의 실시간 푸시 통로이고 로봇 연결과는 별개다. 끊겨도 화면이
          죽지는 않는다(폴링이 백업으로 돈다). */}
      <div className="flex items-center gap-3">
        <div
          className="flex items-center gap-1.5"
          title="관제 서버와의 실시간 연결. 끊기면 폴링으로 갱신되어 반영이 몇 초 늦어집니다. 로봇 연결과는 별개입니다."
        >
          <div className={`w-2 h-2 rounded-full ${wsConnected ? "bg-primary animate-pulse" : "bg-destructive"}`} />
          <span className="font-mono text-[10px] text-muted-foreground">
            관제 서버 {wsConnected ? "연결됨" : "오프라인"}
          </span>
        </div>
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
    <div className={`relative bg-[#12171f] border-b border-border flex-shrink-0 overflow-hidden ${SLOT_CLASS}`}>
      <LiveMap />
    </div>
  );
}

/**
 * 사이드바 폭 (S15P11A301-259).
 *
 * 전에는 메인이 무엇이냐에 따라 폭이 달라졌다 — 영상이 메인이면 16:9 상자가
 * 먹고 남은 폭(실측 208px), 지도가 메인이면 고정 308px 이었다. 같은 페이지에서
 * 패널이 넓어지고 좁아지는 것으로 보였다. 두 값의 평균으로 고정한다.
 */
const SIDEBAR_WIDTH = 260;

/**
 * 사이드바 상단 슬롯 — 폭에서 높이를 유도한다.
 *
 * **고정 높이를 쓰지 않는다.** 예전에는 미니맵 148px·영상 180px 로 두 곳에
 * 다른 숫자가 박혀 있었고, 사이드바 폭을 바꾸면 둘 다 따로 고쳐야 했다.
 * 사이드바 폭이 고정이므로 여기서는 폭이 구속 조건이다.
 */
const SLOT_CLASS = "w-full aspect-video";

/**
 * 메인 영역 — **높이**에서 폭을 유도한다.
 *
 * 슬롯과 방향이 반대인 것은 의도다. `w-full aspect-video max-h-full` 로 두면
 * 16:9 가 깨진다 — 폭이 100% 로 고정된 채 `max-height` 만 걸려서, 세로가
 * 모자란 순간 상자가 16:9 보다 납작해진다. 가로 화면에서는 세로가 먼저
 * 부족하므로 높이를 구속 조건으로 삼는다.
 *
 * `max-w-full` 은 세로로 긴 창에 대한 안전장치다. 그때는 폭이 먼저 부족해
 * 비율이 깨지지만, 관제 화면은 가로 모니터를 전제한다.
 */
const MAIN_CLASS = "h-full aspect-video max-w-full";

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

      {/* 사이드바가 화면 아래까지 관통한다 (S15P11A301-303).
          종전에는 본문(영상+사이드바) 아래에 하단 바가 가로로 깔려서, 사이드바가
          중간에서 끊기고 하단 바가 그 밑을 지나갔다. 사이드바를 끝까지 내리면
          **관측(오른쪽 세로)과 조작(아래 가로)이 시각적으로 갈린다** — 지금
          하단 바에 있는 것은 임무 상태와 명령뿐이라 그 구분이 내용과도 맞는다.

          두 화면이 같은 기하라는 원칙(S15P11A301-259)은 그대로다. 메인이 영상이든
          지도든 골격은 하나이고 내용만 바뀐다. */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        <SideNav />

        {/* 가운데 — 메인 화면과 그 아래 명령 바. 양옆 바를 뺀 나머지를 쓴다. */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
          {/* 메인은 16:9 를 유지하며 남는 공간에 가운데 맞춤한다. */}
          <div className="flex-1 min-h-0 flex items-center justify-center overflow-hidden">
            <div className={`relative overflow-hidden flex flex-col ${MAIN_CLASS}`}>
              {videoMain ? (
                <VideoPanel isMain onSwap={() => setMainView("map")} />
              ) : (
                /* 메인도 같은 실시간 지도다. 예전에는 목업 격자(LidarMap)를 띄워서
                   키우면 실시간 지도가 사라졌다(S15P11A301-227).
                   LiveMap 은 격자를 상자에 맞춰 레터박스 처리하므로 16:9 상자에
                   넣어도 셀이 찌그러지지 않는다. */
                <LiveMap variant="full" />
              )}
            </div>
          </div>

          {/* 하단 명령 바 (S15P11A301-303). 종전에는 빈 띠였다 — 16:9 메인 영역이
              세로를 다 먹지 않게 남긴 자리이자, 조종 바를 뺀 뒤로 비어 있던 곳이다.
              임무 상태·명령을 사이드바에서 여기로 옮겨 그 자리를 쓴다.
              사이드바 아래로는 넘어가지 않는다 — 조작 영역은 메인 화면의 몫이다. */}
          <CommandBar />
        </div>

        <div
          className="flex flex-col border-l border-border bg-card overflow-y-auto flex-shrink-0"
          style={{ width: SIDEBAR_WIDTH }}
        >
          {videoMain ? (
            <MiniMapSlot />
          ) : (
            <VideoPanel onSwap={() => setMainView("video")} />
          )}
          {/* 운행 모드는 하단 명령 바로 옮겼다 (S15P11A301-303) — 조작은 그쪽이 모은다. */}
          <StatusPanel />
          <SensorDashboard />
          {/* 센서(ESP32) 아래 — 엔코더도 같은 보드에서 오므로 결측 원인이 같다
              (S15P11A301-300). */}
          <MotionPanel />
        </div>
      </div>

      <CommandAlertToast />
    </div>
  );
}

/**
 * 명령 결과 알림 (S15P11A301-207). 거부·실패·무응답을 사유와 함께 보여준다 —
 * 202 만 믿고 조용히 원상복귀하던 화면이 이유를 말하게 된다. 8초 뒤 자동으로
 * 사라지고, 새 알림이 오면 시계가 다시 돈다.
 */
function CommandAlertToast() {
  const { commandAlert, dismissCommandAlert } = useRobot();

  useEffect(() => {
    if (!commandAlert) return;
    const timer = setTimeout(dismissCommandAlert, 8000);
    return () => clearTimeout(timer);
  }, [commandAlert, dismissCommandAlert]);

  if (!commandAlert) return null;
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3
                    border border-accent/50 bg-background/95 rounded px-4 py-2.5 shadow-lg">
      <span className="font-mono text-xs text-accent">⚠ {commandAlert}</span>
      <button
        onClick={dismissCommandAlert}
        className="font-mono text-[10px] text-muted-foreground hover:text-foreground transition-colors"
      >
        닫기
      </button>
    </div>
  );
}

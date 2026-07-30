"use client";

import { Navigation, CornerUpLeft, ShieldAlert, Pause } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import type { MissionState, SafetyState } from "@/features/robot/mockData";
import { toast } from "sonner";

/**
 * 임무 상태 패널.
 *
 * 표시 기준은 명세 26.2의 임무 상태 12개와 common/schemas/state.schema.json이다.
 * 관제자가 이 패널을 보고 내리는 판단은 하나다 — "지금 개입해야 하나".
 * 그래서 임무 단계를 가장 크게 두고, 텔레메트리(속도·방위)는 아래로 내렸다.
 */

const MISSION_LABEL: Record<MissionState, string> = {
  SAFE_IDLE: "대기",
  EXPLORING: "탐사 중",
  PERSON_APPROACHING: "접근 중",
  INTERACTING: "음성 확인",
  POST_RECORDING: "사후 녹화",
  REPORTING: "보고 중",
  PAUSED: "일시정지",
  MANUAL: "수동 조종",
  RETURNING: "복귀 중",
  COMPLETED: "임무 완료",
  ESTOP: "비상 정지",
  ERROR: "오류",
};

/** 관제자가 개입해야 하는 단계는 앰버, 정상 진행은 초록, 위험은 레드로 나눈다. */
const MISSION_TONE: Record<MissionState, string> = {
  SAFE_IDLE: "text-muted-foreground border-border bg-muted",
  EXPLORING: "text-primary border-primary/30 bg-primary/10",
  PERSON_APPROACHING: "text-accent border-accent/30 bg-accent/10",
  INTERACTING: "text-accent border-accent/30 bg-accent/10",
  POST_RECORDING: "text-accent border-accent/30 bg-accent/10",
  REPORTING: "text-accent border-accent/30 bg-accent/10",
  PAUSED: "text-accent border-accent/40 bg-accent/10",
  MANUAL: "text-info border-info/30 bg-info/10",
  RETURNING: "text-accent border-accent/30 bg-accent/10",
  COMPLETED: "text-primary border-primary/30 bg-primary/10",
  ESTOP: "text-destructive border-destructive/40 bg-destructive/15",
  ERROR: "text-destructive border-destructive/40 bg-destructive/15",
};

/** 사람을 만난 뒤의 단계들. 이 동안에는 탐사가 멈춰 있다(26.2). */
const ENCOUNTER_PHASES: MissionState[] = [
  "PERSON_APPROACHING",
  "INTERACTING",
  "POST_RECORDING",
  "REPORTING",
];

const SAFETY_LABEL: Record<Exclude<SafetyState, null>, string> = {
  SAFE_IDLE: "안전 대기",
  READY: "준비됨",
  RUNNING: "주행 중",
  STOPPED: "정지",
  ESTOP: "비상 정지",
  FAULT: "결함",
};

function mmss(totalSec: number) {
  const m = Math.floor(totalSec / 60).toString().padStart(2, "0");
  const s = Math.floor(totalSec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

/**
 * 구성요소 표시등. true/false/null을 모두 다르게 보여준다.
 * telemetry.schema.json이 못 박은 대로 false(끊김)와 null(확인 수단 없음)은
 * 다른 사실이므로 같은 회색으로 뭉개면 안 된다.
 */
function HealthDot({ label, ok }: { label: string; ok: boolean | null }) {
  const tone =
    ok === true
      ? { dot: "bg-primary", text: "text-muted-foreground", title: `${label} 정상` }
      : ok === false
        ? { dot: "bg-destructive", text: "text-destructive", title: `${label} 끊김` }
        : { dot: "bg-muted-foreground/40", text: "text-muted-foreground/60", title: `${label} 확인 불가` };

  return (
    <div className="flex items-center gap-1.5" title={tone.title}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${tone.dot}`} />
      <span className={`text-[11px] ${tone.text}`}>{label}</span>
    </div>
  );
}

export default function StatusPanel() {
  const { status, wsConnected, sendCommand } = useRobot();
  const { missionState, controlMode, safetyState, health } = status;

  const remainingSec = Math.max(0, status.explorationLimitSec - status.explorationElapsedSec);
  const elapsedPct = Math.min(
    100,
    (status.explorationElapsedSec / status.explorationLimitSec) * 100,
  );
  const explorationStarted = status.explorationElapsedSec > 0;
  const inEncounter = ENCOUNTER_PHASES.includes(missionState);
  const danger = missionState === "ESTOP" || missionState === "ERROR";

  const components = (() => {
    const all = [
      { label: "Jetson", ok: wsConnected },
      { label: "카메라", ok: health.cameraOk },
      { label: "LiDAR", ok: health.lidarOk },
      { label: "MCU", ok: health.mcuConnected },
    ];
    return { total: all.length, problems: all.filter(c => c.ok !== true) };
  })();

  const handleCommand = async (type: string, label: string) => {
    try {
      await sendCommand(type);
      toast.success(`명령 전송: ${label}`, { duration: 3000 });
    } catch {
      toast.error("명령 실패", { description: "연결 상태를 확인하세요" });
    }
  };

  return (
    <div className="p-3.5 space-y-3 border-b border-border">
      {/* 비상 정지·오류는 다른 모든 것보다 위에, 화면 폭 전체로 알린다.
          배지 색만 바꾸면 평상시와 구분되지 않는다. */}
      {danger && (
        <div className="flex items-center gap-2 rounded border border-destructive/50 bg-destructive/15 px-2.5 py-2">
          <ShieldAlert size={16} className="text-destructive flex-shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold text-destructive leading-tight">
              {MISSION_LABEL[missionState]}
            </p>
            <p className="text-[11px] text-destructive/80">
              해제 후에도 자동으로 재출발하지 않습니다
            </p>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-muted-foreground">임무 상태</span>
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-primary animate-pulse" : "bg-destructive"}`}
          />
          <span className="text-[11px] text-muted-foreground">
            {wsConnected ? "연결됨" : "오프라인"}
          </span>
        </div>
      </div>

      {/* 임무 단계 — 이 패널의 헤드라인.
          원문 상태값은 title로만 노출한다. 한글 라벨 바로 아래에 영문 enum을
          같이 찍으면 같은 정보가 두 줄을 차지한다. */}
      <div
        className={`border rounded px-3 py-2.5 ${MISSION_TONE[missionState]}`}
        title={missionState}
      >
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-xl font-semibold leading-none">{MISSION_LABEL[missionState]}</p>
          <span className="font-mono text-[11px] opacity-70">
            {controlMode === "MANUAL" ? "수동" : controlMode === "AUTO" ? "자율" : "—"}
          </span>
        </div>
      </div>

      {/* 사람 발견 이후 단계에서는 탐사가 멈춘 상태임을 명시한다.
          "접근 중"만 보면 탐사가 계속되는 줄 오해한다. */}
      {inEncounter && (
        <p className="text-[11px] text-accent">탐사 일시정지 · 피해자 확인 절차 진행 중</p>
      )}

      {/* 복귀 경로 실패 등으로 PAUSED가 되면 관제가 복구를 지시해야 한다(23.5). */}
      {missionState === "PAUSED" && (
        <div className="flex items-start gap-1.5 rounded border border-accent/30 bg-accent/5 px-2.5 py-2">
          <Pause size={12} className="text-accent flex-shrink-0 mt-0.5" />
          <p className="text-[11px] text-accent leading-snug">
            재개는 운영자 명령으로만 이루어집니다. 탐사 재개 또는 복귀를 지시하세요.
          </p>
        </div>
      )}

      {/* 잔여 탐사 시간 — 목적지가 미정인 임무에서 유일하게 상한이 있는 진행률.
          출발 전에는 "--:--"만 보여주므로 자리만 차지한다. 그 자리는 출발 전
          점검(구성요소)이 쓰는 게 낫다. */}
      {explorationStarted && (
      <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
        <div className="flex items-baseline justify-between mb-1.5">
          <span className="text-[11px] text-muted-foreground">
            잔여 탐사 시간
            <span className="text-muted-foreground/60"> / 제한 {mmss(status.explorationLimitSec)}</span>
          </span>
          <span className="font-mono text-lg font-semibold text-foreground tabular-nums leading-none">
            {explorationStarted ? mmss(remainingSec) : "--:--"}
          </span>
        </div>
        <div className="h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-1000"
            style={{ width: `${elapsedPct}%` }}
          />
        </div>
      </div>
      )}

      <div className="grid grid-cols-2 gap-2">
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
          <p className="text-[11px] text-muted-foreground mb-0.5">속도</p>
          <p className="font-mono text-base font-semibold text-foreground tabular-nums">
            {status.speed.toFixed(2)}
            <span className="text-[11px] text-muted-foreground font-normal"> m/s</span>
          </p>
        </div>
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/30">
          <p className="text-[11px] text-muted-foreground mb-0.5">방위각</p>
          <p className="font-mono text-base text-foreground tabular-nums">{status.heading}°</p>
        </div>
      </div>

      {/* 구성요소.
          정상일 때 초록 점 4개를 늘어놓는 것은 정보가 아니라 소음이다. 다 정상이면
          한 줄로 줄이고, 출발 전(SAFE_IDLE)에만 점검 결과로 보여준다. 무엇이든
          정상이 아니면 그 항목만 펼친다(시연 시나리오 1번).
          E-Stop은 표시만 한다. 소프트웨어 발동·해제는 임베디드 담당 리뷰와
          실장비 검증이 필요하므로 이 패널에 조작을 두지 않는다. */}
      {(components.problems.length > 0 || missionState === "SAFE_IDLE") && (
        <div className="border border-border rounded px-2.5 py-2 bg-secondary/20 space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium text-muted-foreground">구성요소</span>
            <span
              className={`font-mono text-[10px] ${
                safetyState === "ESTOP" || safetyState === "FAULT"
                  ? "text-destructive"
                  : "text-muted-foreground"
              }`}
            >
              {safetyState ? SAFETY_LABEL[safetyState] : "확인 불가"}
            </span>
          </div>
          {components.problems.length === 0 ? (
            <p className="text-[11px] text-primary">
              {components.total}개 항목 정상 · 출발 가능
            </p>
          ) : (
            <div className="space-y-1">
              {components.problems.map(c => (
                <HealthDot key={c.label} label={c.label} ok={c.ok} />
              ))}
            </div>
          )}
        </div>
      )}

      <div className="space-y-1.5">
        <button
          onClick={() => handleCommand("explore", "탐사 시작")}
          disabled={danger}
          className="w-full text-xs font-medium px-3 py-2 rounded border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          <Navigation size={13} />
          {missionState === "PAUSED" ? "탐사 재개" : "탐사 시작"}
        </button>
        {/* 일시정지 — 주행 중에만 의미가 있다. 재개는 위 버튼이 "탐사 재개"로 바뀐다. */}
        {(missionState === "EXPLORING" || missionState === "PERSON_APPROACHING") && (
          <button
            onClick={() => handleCommand("pause", "일시정지")}
            className="w-full text-xs font-medium px-3 py-2 rounded border border-border bg-secondary/40 text-foreground hover:bg-secondary/70 transition-colors flex items-center justify-center gap-2"
          >
            <Pause size={13} />
            일시정지
          </button>
        )}
        <button
          onClick={() => handleCommand("return", "베이스캠프 복귀")}
          disabled={danger}
          className="w-full text-xs font-medium px-3 py-2 rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          <CornerUpLeft size={13} />
          베이스캠프 복귀
        </button>
      </div>
    </div>
  );
}

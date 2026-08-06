"use client";

import { Navigation, CornerUpLeft, Pause, ShieldAlert, Smartphone } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import { toast } from "sonner";
import { MISSION_LABEL, MISSION_TEXT_TONE, ENCOUNTER_PHASES } from "./StatusPanel";
import ModeRow from "./ModeRow";

/**
 * 하단 명령 바 (S15P11A301-303).
 *
 * 임무 상태와 명령 버튼을 사이드바(폭 260px)에서 화면 하단 가로 전체로 옮겼다.
 * 셋이 바뀐다.
 *
 * 1. **버튼이 커진다.** 260px 안에서 세로로 쌓을 때는 한 줄에 하나씩, 높이 32px
 *    이었다. 가로로 펴면 세 개가 나란히 들어가고 각각이 넓어진다 — 임무를 시작·
 *    정지하는 버튼은 이 화면에서 가장 크게 눌리는 것이라 그만한 크기가 맞다.
 *    바 높이 64px, 버튼 최소 폭 128px 로 잡았다. 가로 폭이 남는다고 더 키우면
 *    영상이 줄고 관제 화면이 조작 패널처럼 보인다 — 이 화면의 주인공은 영상이다.
 * 2. **사이드바에 자리가 생긴다.** 관측값(센서·주행)만 남아 위아래 여백이 준다.
 * 3. **이미 있던 빈 띠를 쓴다.** 하단 40px 은 16:9 메인 영역이 세로를 다 먹지 않게
 *    남겨 둔 자리였다(조종 바를 뺀 뒤로 비어 있었다). 그 자리를 채우는 것이라
 *    영상 크기에 주는 영향이 작다.
 *
 * 상태 표시는 여기서도 한다 — 「무엇을 누를 수 있나」와 「지금 어떤 상태인가」는
 * 붙어 있어야 판단이 된다. 경고·안내도 같은 줄에 둔다.
 */
export default function CommandBar() {
  const { status, sendCommand } = useRobot();
  const { missionState, controlMode } = status;

  const inEncounter = ENCOUNTER_PHASES.includes(missionState);
  const danger = missionState === "ESTOP" || missionState === "ERROR";
  // 수동에서는 자율 명령을 내보내지 않는다 (S15P11A301-259). 모바일 앱이 모터
  // ESP32 에 직접 붙으므로, 수동 중 자율 명령은 같은 모터를 동시에 미는 일이 된다.
  const manual = controlMode === "MANUAL";

  const handleCommand = async (type: string, label: string) => {
    try {
      await sendCommand(type);
      toast.success(`명령 전송: ${label}`, { duration: 3000 });
    } catch {
      toast.error("명령 실패", { description: "연결 상태를 확인하세요" });
    }
  };

  return (
    <div className="h-16 flex-shrink-0 border-t border-border bg-card flex items-center gap-4 px-4">
      {/* 상태 — 바의 왼쪽 고정. 명령 버튼과 눈이 오가는 거리가 짧아야 한다.
          **테두리 상자를 두지 않는다.** 옆의 명령 버튼들이 이미 테두리 상자라
          같이 두면 상태도 누를 수 있는 것으로 읽힌다. 상태는 글자색으로만
          구분한다 — 정상은 초록, 개입이 필요하면 앰버, 위험은 빨강. */}
      <div className="flex items-baseline gap-2.5 flex-shrink-0" title={missionState}>
        <span className={`text-xl font-semibold leading-none ${MISSION_TEXT_TONE[missionState]}`}>
          {MISSION_LABEL[missionState]}
        </span>
        {/* 모를 때는 아예 숨긴다 (S15P11A301-303). 「—」를 띄우면 옆 사이드바의
            운행 모드 토글(자율에 켜져 있다)과 모순돼 보인다 — 토글은 화면 기본값이고
            이 값은 로봇이 보고한 것이라 서로 다른 것을 가리킨다. 로봇이 붙으면
            「탐사 중 · 자율」로 상태와 모드가 한 줄에 읽힌다. */}
        {controlMode !== null && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {controlMode === "MANUAL" ? "수동" : "자율"}
          </span>
        )}
      </div>

      {/* 안내·경고 — 가운데. 남는 폭을 쓰고, 없으면 자리를 차지하지 않는다. */}
      <div className="flex-1 min-w-0">
        {danger && (
          <div className="flex items-center gap-2 text-destructive">
            <ShieldAlert size={15} className="flex-shrink-0" />
            <p className="text-[11px] leading-snug truncate">
              해제 후에도 자동으로 재출발하지 않습니다
            </p>
          </div>
        )}
        {!danger && manual && (
          <div className="flex items-center gap-2 text-info">
            <Smartphone size={13} className="flex-shrink-0" />
            <p className="text-[11px] leading-snug truncate">
              모바일 앱에서 조종합니다 · 자율 명령을 내리려면 「자율」로 되돌리세요
            </p>
          </div>
        )}
        {!danger && !manual && inEncounter && (
          <p className="text-[11px] text-accent truncate">
            탐사 일시정지 · 피해자 확인 절차 진행 중
          </p>
        )}
        {!danger && !manual && !inEncounter && missionState === "PAUSED" && (
          <div className="flex items-center gap-2 text-accent">
            <Pause size={13} className="flex-shrink-0" />
            {/* 「복귀」는 말하지 않는다 (S15P11A301-281) — 그 버튼이 없다. */}
            <p className="text-[11px] leading-snug truncate">
              재개는 운영자 명령으로만 이루어집니다. 「탐사 재개」를 지시하세요.
            </p>
          </div>
        )}
      </div>

      {/* 조작 — 오른쪽 고정. 운행 모드가 먼저 오고 임무 명령이 뒤따른다.
          모드는 「어느 쪽이 조종하나」, 명령은 「무엇을 시키나」라 순서가 그렇다.
          모드 전환은 수동일 때도 보여야 한다 — 자율로 되돌릴 유일한 수단이다. */}
      <div className="flex items-center gap-4 flex-shrink-0">
        <ModeRow />

      {!manual && (
        <div className="flex items-center gap-3 flex-shrink-0">
          <button
            onClick={() => handleCommand("explore", "탐사 시작")}
            disabled={danger}
            className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
          >
            <Navigation size={15} />
            {missionState === "PAUSED" ? "탐사 재개" : "탐사 시작"}
          </button>
          {/* 일시정지 — 주행 중에만 의미가 있다. 재개는 위 버튼이 "탐사 재개"로 바뀐다. */}
          {(missionState === "EXPLORING" || missionState === "PERSON_APPROACHING") && (
            <button
              onClick={() => handleCommand("pause", "일시정지")}
              className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-border bg-secondary/40 text-foreground hover:bg-secondary/70 transition-colors flex items-center justify-center gap-2"
            >
              <Pause size={15} />
              일시정지
            </button>
          )}
          {/* STOP 을 보내 임무를 종료한다. 복귀 주행이 아니다 (S15P11A301-274·246).
              끝낼 임무가 있을 때만 보인다 — 대기·완료에는 종료할 대상이 없다. */}
          {missionState !== "SAFE_IDLE" && missionState !== "COMPLETED" && (
            <button
              onClick={() => handleCommand("return", "임무 종료")}
              disabled={danger}
              className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              <CornerUpLeft size={15} />
              임무 종료
            </button>
          )}
        </div>
      )}
      </div>
    </div>
  );
}

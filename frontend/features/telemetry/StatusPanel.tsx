"use client";

import { Navigation, CornerUpLeft, ShieldAlert, Pause, Smartphone } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import type { MissionState } from "@/features/robot/mockData";
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

export default function StatusPanel() {
  const { status, wsConnected, sendCommand } = useRobot();
  const { missionState, controlMode } = status;

  const inEncounter = ENCOUNTER_PHASES.includes(missionState);
  const danger = missionState === "ESTOP" || missionState === "ERROR";
  // 수동에서는 자율 명령을 내보내지 않는다 (S15P11A301-259).
  //
  // 모바일 앱은 모터 ESP32 에 직접 붙어 조종하고 젯슨을 거치지 않는다(ModeRow
  // 참고). 그래서 수동 중에 「탐사 재개」나 「복귀」를 누르면 앱과 자율이 같은
  // 모터를 동시에 민다 — 수동 진입이 PAUSE 를 먼저 보내는 이유가 그것이다.
  // 버튼을 비활성으로 두지 않고 **숨기는** 쪽을 골랐다. 회색 버튼은 "지금은
  // 안 되지만 여기서 하는 것" 으로 읽히는데, 여기서는 아예 하지 않는다.
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

      {/* 복귀 경로 실패 등으로 PAUSED가 되면 관제가 복구를 지시해야 한다(23.5).
          수동에서는 내보내지 않는다 — 수동 진입이 PAUSE 를 보내므로 수동은 항상
          PAUSED 이고, 이 안내가 가리키는 두 버튼이 아래에서 사라진다. */}
      {!manual && missionState === "PAUSED" && (
        <div className="flex items-start gap-1.5 rounded border border-accent/30 bg-accent/5 px-2.5 py-2">
          <Pause size={12} className="text-accent flex-shrink-0 mt-0.5" />
          <p className="text-[11px] text-accent leading-snug">
            재개는 운영자 명령으로만 이루어집니다. 탐사 재개 또는 복귀를 지시하세요.
          </p>
        </div>
      )}

      {/* 잔여 탐사 시간 게이지를 뺐다 (S15P11A301-223).

          서버에 대응하는 값이 없었다. 제한 시간은 프런트엔드 상수
          (EXPLORATION_LIMIT_SEC = 7분)였고 임무 조회 응답에는 그런 필드가 없다.
          즉 화면의 숫자와 게이지가 전부 프런트가 만든 것이었다.

          딸린 부작용이 더 나빴다. RobotContext 에 제한 시간에 도달하면 임무
          상태를 스스로 RETURNING 으로 바꾸는 1Hz 타이머가 있었고, 실제로
          "탐사 중"인데 잔여 시간 00:00 에 게이지가 꽉 찬 화면을 목격했다.
          설명할 수 없는 표시라 타이머까지 함께 걷어냈다.

          제한 시간으로 탐사를 끝내는 것은 명세 23.4 그대로 유효하다. 그 판단은
          로봇이 하고 화면은 결과(종료 사유)를 받는다 — 프런트가 시계를 따로
          돌리는 것과는 다른 일이다. */}

      {/* 구성요소 블록도 뺐다 (S15P11A301-223).
          Jetson·카메라·LiDAR·MCU 네 표시등이었다. 연결 여부는 이 패널 상단의
          점 하나가 이미 말하고, 나머지 셋은 관제자가 그것을 보고 내릴 판단이
          없다 — 카메라가 끊기면 영상 패널이 직접 알리고, LiDAR 는 지도가,
          MCU 는 주행이 멈추는 것으로 드러난다. health·safetyState 는 서버가
          보내는 값이므로 데이터 모델은 남겨 두었다. */}

      {/* 속도·방위각 타일을 뺐다 (S15P11A301-200).
          둘 다 실데이터 출처가 없다. 속도는 telemetry의 motion인데
          cloud_bridge가 motion=None으로 보내고(엔코더·ESP32 대기, S15P11A301-174),
          방위각은 pose.yaw에 실값이 있지만 그것을 읽는 조회 API가 없다.
          화면에 있던 값은 프론트가 만든 난수였다.
          미니맵 화살표가 방향과 이동을 이미 보여주므로 숫자 타일은 중복이기도
          했다. 실데이터가 오면 큰 타일이 아니라 영상 좌측 상단 오버레이 줄에
          한 줄로 넣는다 — 그쪽이 "관측값" 형식이다. */}

      {manual ? (
        <div className="flex items-start gap-1.5 rounded border border-info/30 bg-info/5 px-2.5 py-2">
          <Smartphone size={12} className="text-info flex-shrink-0 mt-0.5" />
          <div className="min-w-0 space-y-1">
            <p className="text-[11px] text-info leading-snug font-medium">
              모바일 앱에서 조종합니다
            </p>
            <p className="text-[11px] text-muted-foreground leading-snug">
              관제 웹에는 조종 입력이 없습니다. 탐사·복귀를 지시하려면 위 「자율」로
              먼저 되돌리세요 — 모드를 되돌려도 주행은 재개되지 않고, 재개는 그
              뒤에 「탐사 재개」로 지시합니다.
            </p>
          </div>
        </div>
      ) : (
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
        {/* 이 버튼은 STOP 을 보내 임무를 종료한다. 복귀 주행이 아니다
            (S15P11A301-274). RETURNING 이 미구현이라(S15P11A301-246) 복귀
            버튼이 갈 곳이 없어 STOP 에 붙어 있었고, 이름만 "베이스캠프 복귀"
            여서 누르면 복귀할 것으로 읽혔다. 동작대로 이름을 맞춘다.

            내부 타입 "return" 은 그대로 둔다 — RobotContext 의 STOP 매핑을
            건드리지 않는다. 복귀 주행이 구현되면 복귀 버튼을 다시 넣는다. */}
        <button
          onClick={() => handleCommand("return", "임무 종료")}
          disabled={danger}
          className="w-full text-xs font-medium px-3 py-2 rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          <CornerUpLeft size={13} />
          임무 종료
        </button>
      </div>
      )}
    </div>
  );
}

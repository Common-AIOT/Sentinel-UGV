"use client";

import { Navigation, CornerUpLeft, Pause, ShieldAlert, Smartphone } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import { toast } from "sonner";
import { MISSION_LABEL, MISSION_TEXT_TONE, ENCOUNTER_PHASES } from "./StatusPanel";
import { commandBarActions } from "./commandActions";
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
 *
 * ## 지금 가능한 조작만 보여준다 (S15P11A301-318)
 *
 * 종전에는 「탐사 시작」이 위험 상태에서만 비활성이고 **탐사 중에도 눌렸다.** 누르면
 * START 가 나가고 젯슨이 `INVALID_STATE` 로 거부한다 — 화면이 없는 선택지를 권한
 * 것이다. 이제 상태별로 실제 보낼 수 있는 명령만 남긴다.
 *
 * | 상태 | 보이는 버튼 |
 * |---|---|
 * | 대기·임무 완료 | 탐사 시작 |
 * | 일시정지 | 탐사 재개 · 임무 종료 |
 * | 탐사 중·접근 중 | 일시정지 · 임무 종료 |
 * | 음성 확인·사후 녹화·보고 중 | 임무 종료 |
 * | 비상·결함 정지 | 임무 종료(비활성) |
 *
 * ## 운행 모드 토글은 상태와 무관하게 남는다 (S15P11A301-318)
 *
 * **표의 규칙은 임무 명령에만 적용한다.** 모드 전환은 「지금 어느 쪽이 로봇을
 * 조종하나」라 임무 진행 단계와 축이 다르다 — 탐사 중이라고 조종 주체를 못 바꿀
 * 이유가 없고, 오히려 탐사 중이야말로 수동으로 넘겨야 할 때다. 그래서 위 표가
 * 시작·일시정지·종료를 상태별로 걷어내는 동안에도 토글은 자리를 지킨다.
 *
 * 수동일 때 명령 버튼을 통째로 숨기던 분기(S15P11A301-259)는 걷어냈다. 그것이 없어도
 * 자율 주행 명령은 나가지 않는다 — 위 표에서 MANUAL 은 「임무 종료」만 남기기 때문이다.
 * 숨기면 조작자가 임무를 끝낼 수단조차 없이 갇히는데, 실제로 그럴 수 있다: MANUAL·AUTO
 * 는 8/6 실측에서 **14건 전부 `MOTOR_BOARD_NO_ACK` 로 거부**됐다(같은 경로의
 * START·PAUSE·STOP 은 전부 EXECUTED 이므로 MQTT 가 아니라 젯슨↔모터 ESP32 구간 문제다).
 * 자율로 되돌아가지 못하는 상태가 실재하므로, 그 상태에서 종료 버튼이 사라지면 안 된다.
 *
 * 거부가 화면을 오염시키는 문제는 여기서 다루지 않는다 — S15P11A301-316 이 이미
 * 막았다. 거부를 확인하는 즉시 서버 상태로 되돌리므로 낙관적 표시가 남지 않는다.
 */
export default function CommandBar() {
  const { status, sendCommand } = useRobot();
  const { missionState, controlMode } = status;

  const inEncounter = ENCOUNTER_PHASES.includes(missionState);
  const danger = missionState === "ESTOP" || missionState === "ERROR";
  // 로봇이 보고한 값이다 — 화면의 기본값이 아니다. 이제 관제 웹이 모드를 바꾸지
  // 않으므로 이 값은 순수한 관측이고, 안내 문구를 고르는 데만 쓴다.
  const manual = controlMode === "MANUAL";

  // 표 전체는 commandActions 가 갖고 시험이 12개 상태를 전부 검사한다. 조건식을
  // 여기 JSX 에 흩어 두면 어느 상태에서 무엇이 보이는지 따져 볼 수가 없다.
  const actions = commandBarActions(missionState);

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
          토글은 어떤 상태에서도 사라지지 않는다 — 상태별로 걷어내는 것은 임무
          명령뿐이다(S15P11A301-318, 위 주석 참고). */}
      <div className="flex items-center gap-4 flex-shrink-0">
        <ModeRow />
        <div className="flex items-center gap-3 flex-shrink-0">
          {/* 시작·재개 — 대기·완료·일시정지에서만. 탐사 중에 이 버튼이 보이면
              조작자는 누르고, 젯슨은 INVALID_STATE 로 거부한다. */}
          {actions.start && (
            <button
              onClick={() =>
                handleCommand("explore", actions.start === "RESUME" ? "탐사 재개" : "탐사 시작")
              }
              className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 transition-colors flex items-center justify-center gap-2"
            >
              <Navigation size={15} />
              {actions.start === "RESUME" ? "탐사 재개" : "탐사 시작"}
            </button>
          )}
          {/* 일시정지 — 주행 중에만 의미가 있다. 재개는 위 버튼이 "탐사 재개"로 바뀐다. */}
          {actions.pause && (
            <button
              onClick={() => handleCommand("pause", "일시정지")}
              className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-border bg-secondary/40 text-foreground hover:bg-secondary/70 transition-colors flex items-center justify-center gap-2"
            >
              <Pause size={15} />
              일시정지
            </button>
          )}
          {/* STOP 을 보내 임무를 종료한다. 복귀 주행이 아니다 (S15P11A301-274·246).
              끝낼 임무가 있을 때만 보인다 — 대기·완료에는 종료할 대상이 없다.
              비상·결함 정지에서는 **숨기지 않고 비활성으로 둔다.** 사람이 물리적으로
              해제해야 하는 상태라(26.5) 버튼이 사라지면 무엇을 기다리는지 알 수 없다. */}
          {actions.stop && (
            <button
              onClick={() => handleCommand("return", "임무 종료")}
              disabled={actions.stopDisabled}
              className="text-sm font-medium min-w-[128px] px-6 py-3 rounded border border-accent/30 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              <CornerUpLeft size={15} />
              임무 종료
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

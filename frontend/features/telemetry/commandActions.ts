import type { MissionState } from "@/features/robot/mockData";

/**
 * 상태별로 하단 명령 바가 보여줄 조작 (S15P11A301-318).
 *
 * 화면에서 빼낸 이유는 실제 사고 때문이다. 종전 규칙은 「탐사 시작」이 위험 상태에서만
 * 비활성이고 나머지 전부에서 눌렸다 — 탐사 중에도 보였고, 누르면 START 가 나가 젯슨이
 * `INVALID_STATE` 로 거부했다. 화면이 없는 선택지를 권한 것이다.
 *
 * 규칙이 JSX 조건식으로 흩어져 있으면 12개 상태를 전부 따져 볼 수가 없다. 관제 화면은
 * jsdom 없이 시험하므로(순수 함수만 검사한다) 판정을 여기로 꺼내야 시험이 표 전체를
 * 지킬 수 있다. environmentThresholds·motionReading 과 같은 이유다.
 *
 * | 상태 | 시작·재개 | 일시정지 | 임무 종료 |
 * |---|---|---|---|
 * | SAFE_IDLE · COMPLETED | 탐사 시작 | | |
 * | PAUSED | 탐사 재개 | | ○ |
 * | EXPLORING · PERSON_APPROACHING | | ○ | ○ |
 * | INTERACTING · POST_RECORDING · REPORTING | | | ○ |
 * | MANUAL · RETURNING | | | ○ |
 * | ESTOP · ERROR | | | 비활성 |
 *
 * 어느 상태에서도 조작이 0개가 되지 않는다는 것이 이 표의 불변식이다. 하나도 없으면
 * 조작자는 임무를 끝낼 수단조차 없이 갇힌다.
 */
export interface CommandBarActions {
  /** 보낼 명령. 없으면 버튼을 내린다 — 시작과 재개는 한 버튼이고 명령만 다르다. */
  start: "START" | "RESUME" | null;
  pause: boolean;
  stop: boolean;
  /**
   * 종료 버튼을 **숨기지 않고 비활성으로** 두는 경우. 비상·결함 정지는 사람이
   * 물리적으로 확인하고 해제해야 하는 상태라(26.5) 버튼이 사라지면 조작자가
   * 무엇을 기다리는지 알 수 없다.
   */
  stopDisabled: boolean;
}

/** 끝낼 임무가 없는 상태. COMPLETED 는 이미 닫혔고 SAFE_IDLE 은 아직 없다. */
const IDLE: MissionState[] = ["SAFE_IDLE", "COMPLETED"];

/** 주행 중이라 멈출 것이 있는 상태. 접근 중도 주행이다. */
const DRIVING: MissionState[] = ["EXPLORING", "PERSON_APPROACHING"];

/** 사람이 해제해야 풀리는 래치 상태(26.5). 어떤 명령도 받지 않는다. */
const LATCHED: MissionState[] = ["ESTOP", "ERROR"];

export function commandBarActions(missionState: MissionState): CommandBarActions {
  const idle = IDLE.includes(missionState);
  const latched = LATCHED.includes(missionState);

  return {
    // PAUSED 만 RESUME 이다. 나머지 두 상태는 임무를 새로 시작한다.
    start: latched ? null : missionState === "PAUSED" ? "RESUME" : idle ? "START" : null,
    pause: !latched && DRIVING.includes(missionState),
    stop: !idle,
    stopDisabled: latched,
  };
}

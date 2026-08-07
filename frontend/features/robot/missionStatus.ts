import type { MissionState } from "./mockData";

/**
 * 서버 `missions.status` → 관제 화면 MissionState.
 *
 * **이 표에 빠진 값이 「탐사 중 고착」의 절반이었다** (S15P11A301-316). 서버는 이제
 * 로봇이 보고한 임무 상태를 그대로 담는데(`RobotStateWriter`), 표에 없는 값이 오면
 * 화면은 조용히 **옛 상태를 계속 보여준다** — 차체가 멈춰 있는데 「탐사 중」인
 * 화면이 그렇게 만들어졌다. 그래서 26.2 의 12개를 전수로 적고, 별도 모듈로 꺼내
 * 시험이 지키게 한다(`motionFromLatest` 와 같은 이유, #300).
 *
 * `CREATED` 는 서버에만 있는 값이다 — 임무 행을 만들었지만 START ACK 이 아직
 * 없는 구간이며, 로봇은 그때 SAFE_IDLE 이다.
 *
 * `MANUAL` 은 S15P11A301-298 에서 추가됐다. 종전에는 서버가 MANUAL 을 몰라
 * (그리고 이 표에도 없어) 3초 폴링이 수동 표시를 즉시 EXPLORING 으로 덮었고,
 * 그 결과 `controlMode` 만 MANUAL 로 남아 두 값이 어긋났다.
 */
export const SERVER_MISSION_STATE: Record<string, MissionState> = {
  CREATED: "SAFE_IDLE",
  SAFE_IDLE: "SAFE_IDLE",
  EXPLORING: "EXPLORING",
  PERSON_APPROACHING: "PERSON_APPROACHING",
  INTERACTING: "INTERACTING",
  POST_RECORDING: "POST_RECORDING",
  REPORTING: "REPORTING",
  PAUSED: "PAUSED",
  MANUAL: "MANUAL",
  RETURNING: "RETURNING",
  COMPLETED: "COMPLETED",
  ESTOP: "ESTOP",
  ERROR: "ERROR",
};

/**
 * 모르는 값이면 `null` 이다 — 호출부는 표시를 바꾸지 않는다. 지어내는 것보다
 * 옛 값을 두는 편이 나은 유일한 경우다: 서버도 모르는 상태는 쓰지 않으므로
 * (`RobotStateWriter` 의 전수 집합) 여기 오는 미지의 값은 계약 위반이다.
 */
export function missionStateFromServer(serverStatus: string): MissionState | null {
  return SERVER_MISSION_STATE[serverStatus] ?? null;
}

/** 임무 목록에서 활성 여부 판정에 필요한 부분. `MissionSummary` 의 부분집합이다. */
export interface ActiveMissionCandidate {
  id: string;
  startedAt: string | null;
  endedAt: string | null;
}

/**
 * 화면이 이어받을 활성 임무를 고른다 (S15P11A301-327).
 *
 * 활성은 `endedAt === null` 이다. 문제는 **그런 임무가 둘 이상 남을 수 있다**는
 * 점이다 — 젯슨이 재시작하면 진행 중이던 임무가 닫히지 않은 채 남고(S15P11A301-322
 * 가 겪은 그 상황이다), 다음 임무가 새로 만들어진다. 그때 옛 임무를 이어받으면
 * 화면은 **아무 telemetry 도 붙지 않는 죽은 임무**를 붙들게 된다.
 *
 * 그래서 **가장 최근에 시작한 것**을 고른다. 배열 순서에 기대지 않는다 — 서버가
 * 최신순으로 준다는 약속이 있지만, 그 약속이 바뀌는 날 화면은 조용히 엉뚱한
 * 임무를 붙든다. 정렬 규칙이 화면 밖에 있고 시험이 지키는 편이 낫다.
 *
 * `startedAt` 이 없는 임무(CREATED 직후, START ACK 전)는 시작한 적이 없으므로
 * 가장 뒤로 민다. 다른 후보가 없을 때만 고른다 — 그때는 그것이 유일한 진행 중
 * 임무이므로 이어받는 것이 맞다.
 */
export function pickActiveMission<T extends ActiveMissionCandidate>(missions: T[]): T | null {
  let best: T | null = null;
  let bestAt = Number.NEGATIVE_INFINITY;
  for (const m of missions) {
    if (m.endedAt !== null) continue;
    const at = m.startedAt === null ? Number.NEGATIVE_INFINITY : Date.parse(m.startedAt);
    // `>=` 가 아니라 `>` 다. 시작 시각이 같으면 먼저 만난 것을 유지한다 — 서버가
    // 최신순으로 주므로 그쪽이 최신이다.
    if (best === null || at > bestAt) {
      best = m;
      bestAt = at;
    }
  }
  return best;
}

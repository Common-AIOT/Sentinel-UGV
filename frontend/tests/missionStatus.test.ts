import { describe, expect, it } from "vitest";

import {
  SERVER_MISSION_STATE,
  missionStateFromServer,
  pickActiveMission,
} from "@/features/robot/missionStatus";
import { MISSION_LABEL } from "@/features/telemetry/StatusPanel";

/**
 * 임무 상태 표 시험 (S15P11A301-316).
 *
 * 이 표에 값이 빠지면 화면은 **옛 상태를 그대로 들고 있는다**. 그렇게 「차체는
 * 멈춰 있는데 탐사 중」이 만들어졌으므로, 빠짐을 시험이 막는다.
 */

/** 젯슨 임무 상태 머신(26.2)의 전수. `mission_state.MissionState` 와 같아야 한다. */
const ROBOT_MISSION_STATES = [
  "SAFE_IDLE",
  "EXPLORING",
  "PERSON_APPROACHING",
  "INTERACTING",
  "POST_RECORDING",
  "REPORTING",
  "PAUSED",
  "MANUAL",
  "RETURNING",
  "COMPLETED",
  "ESTOP",
  "ERROR",
];

describe("missionStateFromServer", () => {
  it("로봇이 보고할 수 있는 12개 상태를 전부 받는다", () => {
    for (const state of ROBOT_MISSION_STATES) {
      expect(missionStateFromServer(state), `${state} 가 표에 없다`).not.toBeNull();
    }
  });

  it("로봇 상태는 이름을 바꾸지 않고 그대로 옮긴다", () => {
    // 서버가 담아 오는 값이 곧 로봇의 상태다. 여기서 이름을 갈아치우면 관제와
    // 로봇 로그가 다른 어휘로 같은 것을 말하게 된다.
    for (const state of ROBOT_MISSION_STATES) {
      expect(missionStateFromServer(state)).toBe(state);
    }
  });

  it("CREATED 는 대기로 옮긴다 — 서버에만 있는 값이다", () => {
    // 임무 행은 만들었지만 START ACK 이 아직 없는 구간이고, 로봇은 SAFE_IDLE 이다.
    expect(missionStateFromServer("CREATED")).toBe("SAFE_IDLE");
  });

  it("모르는 값은 null 이다 — 표시를 바꾸지 않는다", () => {
    expect(missionStateFromServer("TELEPORTING")).toBeNull();
    expect(missionStateFromServer("")).toBeNull();
  });

  it("옮긴 값은 모두 화면 라벨을 갖는다", () => {
    // 라벨이 없으면 상태 칸이 빈다. 두 표가 갈라지는 것을 여기서 잡는다.
    for (const mapped of Object.values(SERVER_MISSION_STATE)) {
      expect(MISSION_LABEL[mapped], `${mapped} 라벨이 없다`).toBeTruthy();
    }
  });
});

/**
 * 활성 임무 고르기 (S15P11A301-327).
 *
 * 화면은 이 결과에 실시간 채널 전체를 건다 — 구독도 폴링도 missionId 로 묶여
 * 있으므로, 여기서 엉뚱한 임무를 고르면 **아무 telemetry 도 붙지 않는 죽은 임무**를
 * 붙든 채 화면이 조용히 멈춘다. 젯슨 재시작으로 닫히지 않은 임무가 남는 것은
 * 실제로 관측된 상황이다(S15P11A301-322).
 */
describe("pickActiveMission", () => {
  const m = (id: string, startedAt: string | null, endedAt: string | null) =>
    ({ id, startedAt, endedAt });

  it("끝나지 않은 임무가 없으면 null 이다", () => {
    expect(pickActiveMission([])).toBeNull();
    expect(pickActiveMission([
      m("a", "2026-08-07T01:00:00Z", "2026-08-07T01:10:00Z"),
      m("b", "2026-08-07T02:00:00Z", "2026-08-07T02:10:00Z"),
    ])).toBeNull();
  });

  it("끝나지 않은 임무 하나면 그것을 고른다", () => {
    const picked = pickActiveMission([
      m("done", "2026-08-07T01:00:00Z", "2026-08-07T01:10:00Z"),
      m("live", "2026-08-07T02:00:00Z", null),
    ]);
    expect(picked?.id).toBe("live");
  });

  // 이것이 이 함수를 만든 이유다. 젯슨이 재시작하면 진행 중이던 임무가 닫히지
  // 않은 채 남고 다음 임무가 새로 만들어진다.
  it("끝나지 않은 임무가 둘이면 가장 최근에 시작한 것을 고른다", () => {
    const picked = pickActiveMission([
      m("stale", "2026-08-07T01:00:00Z", null),
      m("current", "2026-08-07T03:00:00Z", null),
    ]);
    expect(picked?.id).toBe("current");
  });

  it("배열 순서에 기대지 않는다 — 오래된 것이 앞에 와도 최신을 고른다", () => {
    // 서버는 최신순으로 주지만 그 약속이 바뀌는 날 화면이 조용히 틀리면 안 된다.
    const oldestFirst = [
      m("stale", "2026-08-07T01:00:00Z", null),
      m("mid", "2026-08-07T02:00:00Z", null),
      m("current", "2026-08-07T03:00:00Z", null),
    ];
    expect(pickActiveMission(oldestFirst)?.id).toBe("current");
    expect(pickActiveMission([...oldestFirst].reverse())?.id).toBe("current");
  });

  it("끝난 임무가 더 최근이어도 고르지 않는다", () => {
    const picked = pickActiveMission([
      m("recent-but-done", "2026-08-07T05:00:00Z", "2026-08-07T05:10:00Z"),
      m("live", "2026-08-07T01:00:00Z", null),
    ]);
    expect(picked?.id).toBe("live");
  });

  it("아직 시작 전(startedAt 없음)인 임무는 뒤로 민다", () => {
    const picked = pickActiveMission([
      m("created", null, null),
      m("running", "2026-08-07T01:00:00Z", null),
    ]);
    expect(picked?.id).toBe("running");
  });

  it("시작 전 임무뿐이면 그것을 고른다 — 유일한 진행 중 임무다", () => {
    expect(pickActiveMission([m("created", null, null)])?.id).toBe("created");
  });

  it("시작 시각이 같으면 앞의 것을 유지한다 — 서버 최신순을 따른다", () => {
    const picked = pickActiveMission([
      m("first", "2026-08-07T01:00:00Z", null),
      m("second", "2026-08-07T01:00:00Z", null),
    ]);
    expect(picked?.id).toBe("first");
  });
});

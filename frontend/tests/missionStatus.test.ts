import { describe, expect, it } from "vitest";

import {
  SERVER_MISSION_STATE,
  missionStateFromServer,
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

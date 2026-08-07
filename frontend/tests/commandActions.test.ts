import { describe, it, expect } from "vitest";
import { commandBarActions } from "@/features/telemetry/commandActions";
import type { MissionState } from "@/features/robot/mockData";

/**
 * 하단 명령 바의 상태별 조작 표 (S15P11A301-318).
 *
 * 12개 상태를 전부 적는다. 몇 개만 뽑아 검사하면 새 상태가 늘거나 규칙이 바뀔 때
 * 빠진 칸이 조용히 틀린다 — 이 화면에서 그것은 「누를 수 없는 버튼을 권한다」거나
 * 「임무를 끝낼 수단이 사라진다」로 나타난다.
 */
const ALL: MissionState[] = [
  "SAFE_IDLE", "EXPLORING", "PERSON_APPROACHING", "INTERACTING",
  "POST_RECORDING", "REPORTING", "PAUSED", "MANUAL",
  "RETURNING", "COMPLETED", "ESTOP", "ERROR",
];

describe("commandBarActions — 시작·재개 버튼", () => {
  it("대기와 임무 완료에서 START 를 보낸다", () => {
    expect(commandBarActions("SAFE_IDLE").start).toBe("START");
    expect(commandBarActions("COMPLETED").start).toBe("START");
  });

  it("일시정지에서만 RESUME 이다", () => {
    expect(commandBarActions("PAUSED").start).toBe("RESUME");
    const others = ALL.filter(s => s !== "PAUSED");
    for (const s of others) expect(commandBarActions(s).start).not.toBe("RESUME");
  });

  // 이 티켓의 핵심. 종전에는 탐사 중에도 「탐사 시작」이 눌렸고 젯슨이 INVALID_STATE
  // 로 거부했다.
  it("탐사 중·접근 중에는 시작 버튼이 없다", () => {
    expect(commandBarActions("EXPLORING").start).toBeNull();
    expect(commandBarActions("PERSON_APPROACHING").start).toBeNull();
  });

  it("피해자 확인 절차 중에는 시작 버튼이 없다", () => {
    for (const s of ["INTERACTING", "POST_RECORDING", "REPORTING"] as MissionState[]) {
      expect(commandBarActions(s).start).toBeNull();
    }
  });

  it("수동·복귀 중에는 시작 버튼이 없다", () => {
    expect(commandBarActions("MANUAL").start).toBeNull();
    expect(commandBarActions("RETURNING").start).toBeNull();
  });

  it("비상·결함 정지에서는 시작 버튼이 없다", () => {
    expect(commandBarActions("ESTOP").start).toBeNull();
    expect(commandBarActions("ERROR").start).toBeNull();
  });
});

describe("commandBarActions — 일시정지 버튼", () => {
  it("주행 중일 때만 보인다", () => {
    for (const s of ALL) {
      const expected = s === "EXPLORING" || s === "PERSON_APPROACHING";
      expect(commandBarActions(s).pause).toBe(expected);
    }
  });
});

describe("commandBarActions — 임무 종료 버튼", () => {
  it("끝낼 임무가 없는 대기·완료에서는 숨는다", () => {
    expect(commandBarActions("SAFE_IDLE").stop).toBe(false);
    expect(commandBarActions("COMPLETED").stop).toBe(false);
  });

  it("임무가 살아 있는 나머지 상태에서는 보인다", () => {
    const alive = ALL.filter(s => s !== "SAFE_IDLE" && s !== "COMPLETED");
    for (const s of alive) expect(commandBarActions(s).stop).toBe(true);
  });

  // 숨기지 않는 것이 핵심이다. 사람이 해제해야 풀리는 상태라 버튼이 사라지면
  // 조작자가 무엇을 기다리는지 알 수 없다.
  it("비상·결함 정지에서는 보이되 비활성이다", () => {
    for (const s of ["ESTOP", "ERROR"] as MissionState[]) {
      expect(commandBarActions(s).stop).toBe(true);
      expect(commandBarActions(s).stopDisabled).toBe(true);
    }
  });

  it("그 밖의 상태에서는 비활성이 아니다", () => {
    const normal = ALL.filter(s => s !== "ESTOP" && s !== "ERROR");
    for (const s of normal) expect(commandBarActions(s).stopDisabled).toBe(false);
  });
});

describe("commandBarActions — 불변식", () => {
  // 조작이 하나도 없으면 조작자는 임무를 끝낼 수단조차 없이 갇힌다. 수동에서
  // 명령 버튼을 통째로 숨기던 종전 분기가 정확히 그 상태를 만들었다.
  it("어떤 상태에서도 조작이 0개가 되지 않는다", () => {
    for (const s of ALL) {
      const a = commandBarActions(s);
      const count = (a.start ? 1 : 0) + (a.pause ? 1 : 0) + (a.stop ? 1 : 0);
      expect(count, `${s} 에서 조작이 없다`).toBeGreaterThan(0);
    }
  });

  it("시작과 일시정지가 동시에 보이지 않는다", () => {
    // 둘이 함께 보이면 「지금 주행 중인가」가 화면에서 모순된다.
    for (const s of ALL) {
      const a = commandBarActions(s);
      expect(a.start !== null && a.pause, `${s} 에서 시작과 일시정지가 함께 보인다`).toBe(false);
    }
  });
});

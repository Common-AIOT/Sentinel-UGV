import { describe, expect, it } from "vitest";

import {
  MEDIA_STATE_LABEL,
  RECORDER_SETTLE_SEC,
  classifyMissingMedia,
  mediaStateLabel,
  summarizeMissionRecorder,
  type RecorderSample,
} from "@/features/telemetry/recorderStatus";

/**
 * 영상 없는 발견의 사유 판정 시험 (S15P11A301-311).
 *
 * 이 판정이 틀리면 화면은 **조용히 거짓말한다** — 실패를 「업로드 중」으로 보여 주면
 * 아무도 젯슨을 열어 보지 않고, 반대로 정상을 실패로 보여 주면 래치(성공이 사유를
 * 지우지 않는 규칙, S15P11A301-309)가 무의미해진다. 둘 다 눈으로는 구별되지 않는다.
 *
 * S15P11A301-304 의 PTS 동률 결함이 19건 쌓일 때까지 드러나지 않았던 자리이므로,
 * 실측에서 나온 패턴(실패·실패·성공)을 시험으로 고정한다.
 */

/** 발견은 T+0 에 시작해 T+60초에 끝난다. 마감은 그 뒤에 일어난다. */
const ENCOUNTER = { startedAt: "2026-08-07T01:00:00.000Z", endedAt: "2026-08-07T01:01:00.000Z" };

function at(secondsAfterEnd: number, ok: boolean | null, failure: string | null = null): RecorderSample {
  const time = new Date(Date.parse(ENCOUNTER.endedAt) + secondsAfterEnd * 1000).toISOString();
  return { time, recorderOk: ok, recorderLastFailure: failure };
}

describe("classifyMissingMedia", () => {
  it("발견 마감이 실패했으면 사유와 함께 실패로 판정한다", () => {
    const verdict = classifyMissingMedia(ENCOUNTER, [
      at(-30, true, null),
      at(10, false, "RECORDING_FAILED_PTS_REGRESSION"),
    ]);
    expect(verdict.kind).toBe("RECORDING_FAILED");
    expect(verdict.rawReason).toBe("RECORDING_FAILED_PTS_REGRESSION");
    expect(verdict.message).toContain("역행");
  });

  it("래치된 이전 실패는 이 발견의 실패가 아니다 — recorderOk 가 true 면 업로드 전이다", () => {
    // 성공은 사유를 지우지 않는다(S15P11A301-309). 사유가 있다는 것만으로 실패로
    // 그리면 한 번 실패한 기동의 모든 발견이 실패로 보인다.
    const verdict = classifyMissingMedia(ENCOUNTER, [
      at(-30, false, "RECORDING_FAILED_PTS_REGRESSION"),
      at(10, true, "RECORDING_FAILED_PTS_REGRESSION"),
    ]);
    expect(verdict.kind).toBe("UPLOAD_PENDING");
    expect(verdict.rawReason).toBeNull();
  });

  it("실패·실패·성공(304 실측 패턴)에서 성공한 세 번째 발견은 업로드 전이다", () => {
    const first = { startedAt: "2026-08-07T01:00:00.000Z", endedAt: "2026-08-07T01:01:00.000Z" };
    const second = { startedAt: "2026-08-07T01:05:00.000Z", endedAt: "2026-08-07T01:06:00.000Z" };
    const third = { startedAt: "2026-08-07T01:10:00.000Z", endedAt: "2026-08-07T01:11:00.000Z" };
    const points: RecorderSample[] = [
      { time: "2026-08-07T01:01:10.000Z", recorderOk: false, recorderLastFailure: "RECORDING_FAILED_PTS_REGRESSION" },
      { time: "2026-08-07T01:06:10.000Z", recorderOk: false, recorderLastFailure: "RECORDING_FAILED_PTS_REGRESSION" },
      { time: "2026-08-07T01:11:10.000Z", recorderOk: true, recorderLastFailure: "RECORDING_FAILED_PTS_REGRESSION" },
    ];
    expect(classifyMissingMedia(first, points, second.startedAt).kind).toBe("RECORDING_FAILED");
    expect(classifyMissingMedia(second, points, third.startedAt).kind).toBe("RECORDING_FAILED");
    expect(classifyMissingMedia(third, points, null).kind).toBe("UPLOAD_PENDING");
  });

  it("같은 사유가 반복돼 문자열이 그대로여도 recorderOk 가 false 면 실패다", () => {
    // 사유 변화만 보고 판정하면 두 번째 PTS 실패를 놓친다 — 래치 값이 같기 때문이다.
    const verdict = classifyMissingMedia(ENCOUNTER, [
      at(-30, false, "RECORDING_FAILED_PTS_REGRESSION"),
      at(20, false, "RECORDING_FAILED_PTS_REGRESSION"),
    ]);
    expect(verdict.kind).toBe("RECORDING_FAILED");
    expect(verdict.rawReason).toBe("RECORDING_FAILED_PTS_REGRESSION");
  });

  it("마감 직후 창 앞쪽의 직전 결과가 아니라 창의 마지막 판정을 따른다", () => {
    // 마감은 POST_RECORDING 3초 뒤에 시작하므로 창 앞쪽에는 직전 마감의 false 가
    // 아직 실려 있다. 첫 점을 보면 성공한 발견이 실패로 뒤집힌다.
    const verdict = classifyMissingMedia(ENCOUNTER, [
      at(5, false, "RECORDING_FAILED_NO_SEGMENTS"),
      at(25, true, "RECORDING_FAILED_NO_SEGMENTS"),
    ]);
    expect(verdict.kind).toBe("UPLOAD_PENDING");
  });

  it("recorderOk 가 전부 null 이면 「정상」이 아니라 판정 근거 없음이다", () => {
    const verdict = classifyMissingMedia(ENCOUNTER, [at(10, null), at(30, null)]);
    expect(verdict.kind).toBe("NO_EVIDENCE");
    expect(verdict.rawReason).toBeNull();
  });

  it("발견 이후의 텔레메트리가 없으면 판정 근거 없음이다", () => {
    const verdict = classifyMissingMedia(ENCOUNTER, [at(-30, true), at(-10, true)]);
    expect(verdict.kind).toBe("NO_EVIDENCE");
  });

  it("마감 창을 넘어선 점은 이 발견에 귀속시키지 않는다", () => {
    const verdict = classifyMissingMedia(ENCOUNTER, [
      at(RECORDER_SETTLE_SEC + 10, false, "RECORDING_FAILED_UNEXPECTED"),
    ]);
    expect(verdict.kind).toBe("NO_EVIDENCE");
  });

  it("다음 발견의 마감 결과를 이 발견에 붙이지 않는다", () => {
    // 발견이 촘촘하면 창(2분)이 다음 발견의 마감까지 덮는다. 그 실패는 다음
    // 발견의 것이므로 창의 끝을 다음 발견 시작으로 당긴다.
    const nextStart = new Date(Date.parse(ENCOUNTER.endedAt) + 30_000).toISOString();
    const points = [at(40, false, "RECORDING_FAILED_DISK_FULL")];
    expect(classifyMissingMedia(ENCOUNTER, points, nextStart).kind).toBe("NO_EVIDENCE");
    expect(classifyMissingMedia(ENCOUNTER, points, null).kind).toBe("RECORDING_FAILED");
  });

  it("진행 중 발견(endedAt 없음)은 시작 시각을 기준으로 본다", () => {
    const ongoing = { startedAt: ENCOUNTER.startedAt, endedAt: null };
    const points = [{
      time: "2026-08-07T01:00:30.000Z",
      recorderOk: false,
      recorderLastFailure: "RECORDING_FAILED_NO_SEGMENTS",
    }];
    expect(classifyMissingMedia(ongoing, points).kind).toBe("RECORDING_FAILED");
  });

  it("실패는 보고됐지만 사유가 없으면 사유 없이 실패라고 말한다", () => {
    const verdict = classifyMissingMedia(ENCOUNTER, [at(10, false, null)]);
    expect(verdict.kind).toBe("RECORDING_FAILED");
    expect(verdict.rawReason).toBeNull();
    expect(verdict.message).toContain("사유가 보고되지 않았습니다");
  });
});

describe("mediaStateLabel", () => {
  /** 젯슨이 실제로 만드는 값(05장 32-5·S15P11A301-310 계약). */
  const OBSERVABLE_STATES = [
    "RECORDING_FAILED_PTS_REGRESSION",
    "RECORDING_FAILED_NO_SEGMENTS",
    "RECORDING_FAILED_DISK_FULL",
    "RECORDING_FAILED_UNEXPECTED",
    "CORRUPT",
  ];

  it("관측 가능한 사유는 모두 한국어 문구가 있다", () => {
    for (const state of OBSERVABLE_STATES) {
      expect(MEDIA_STATE_LABEL[state], `${state} 가 표에 없다`).toBeTruthy();
    }
  });

  it("모르는 사유는 원문을 그대로 보여 준다", () => {
    // 젯슨이 `RECORDING_FAILED_{사유}` 로 만들므로 값이 늘어난다. 표에 없는 값을
    // 「알 수 없는 오류」로 뭉개면 젯슨과 대조할 실마리가 사라진다.
    expect(mediaStateLabel("RECORDING_FAILED_SOMETHING_NEW")).toBe("RECORDING_FAILED_SOMETHING_NEW");
  });
});

describe("summarizeMissionRecorder", () => {
  it("사유가 남아 있으면 실패 기록으로 본다 — recorderOk 가 true 여도 그렇다", () => {
    const summary = summarizeMissionRecorder([
      at(10, true, "RECORDING_FAILED_PTS_REGRESSION"),
    ]);
    expect(summary.state).toBe("FAILED");
    expect(summary.lastFailure).toBe("RECORDING_FAILED_PTS_REGRESSION");
  });

  it("마지막 사유는 배열 순서가 아니라 시각으로 고른다", () => {
    const summary = summarizeMissionRecorder([
      at(30, false, "RECORDING_FAILED_NO_SEGMENTS"),
      at(10, false, "RECORDING_FAILED_PTS_REGRESSION"),
    ]);
    expect(summary.lastFailure).toBe("RECORDING_FAILED_NO_SEGMENTS");
  });

  it("사유가 없고 정상 판정만 있으면 정상이다", () => {
    expect(summarizeMissionRecorder([at(10, true), at(20, true)])).toEqual({
      state: "OK",
      lastFailure: null,
    });
  });

  it("판정값이 없으면 정상이 아니라 근거 없음이다", () => {
    // recorder 없이 도는 구성과 아직 마감한 이벤트가 없는 임무가 여기 해당한다.
    expect(summarizeMissionRecorder([at(10, null), at(20, null)]).state).toBe("NO_EVIDENCE");
    expect(summarizeMissionRecorder([]).state).toBe("NO_EVIDENCE");
  });
});

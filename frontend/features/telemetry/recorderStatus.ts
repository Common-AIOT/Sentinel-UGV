/**
 * 녹화기 상태(telemetry `health`) 판정 (S15P11A301-311).
 *
 * 「영상 없는 발견」은 세 가지가 한 모습으로 보이던 자리다 — 마감이 실패해 **영원히
 * 영상이 없는 것**, 아직 **업로드 전**인 것, 그리고 **판정할 근거 자체가 없는 것**.
 * 앞은 젯슨을 열어 봐야 하고 뒤는 기다리면 되므로 대응이 다르다. S15P11A301-304 의
 * PTS 동률 결함이 19건 쌓일 때까지 아무도 몰랐던 마지막 고리가 이 구별의 부재였다.
 *
 * 판정 근거는 encounter 가 아니라 **임무 시계열**이다. 실패한 이벤트는 `event.mp4` 가
 * 없어 업로드 경로를 아예 타지 않으므로(`pending_store.ready_for_upload` 가
 * `has_media and has_checksum`) 사유가 미디어 행에 실려 올 길이 없다. 사유는
 * telemetry `health.recorderOk`·`recorderLastFailure` 로만 온다(S15P11A301-309·310).
 * 그래서 여기서 **발견 시각과 텔레메트리 점을 맞춰** 귀속시킨다.
 *
 * 두 필드의 의미를 섞지 않는 것이 핵심이다.
 *
 * - `recorderOk` 는 **마지막으로 마감한 이벤트**가 영상을 남겼는가다. `null` 은
 *   「정상」이 아니라 「판정 근거 없음」이다(recorder 없는 구성, 아직 이벤트 없음).
 * - `recorderLastFailure` 는 젯슨이 **래치**한다 — 성공해도 지워지지 않는다. 따라서
 *   `recorderOk: true` 와 사유가 함께 오는 것은 정상 조합이며
 *   「지금은 정상이지만 이번 기동에 실패가 있었다」는 뜻이다. 이것을 실패로 그리면
 *   래치가 무의미해진다. 래치를 남긴 이유가 재발 감지이므로 지우지도 않는다.
 *
 * 래치 때문에 「사유가 있다」만으로는 이 발견의 실패인지 알 수 없다. 그래서 발견
 * 마감 창 안의 **가장 늦은 판정값**(`recorderOk` 가 null 이 아닌 마지막 점)을 본다 —
 * 마감은 이 발견 뒤에 일어나고 그 결과가 곧 그 시점의 `recorderOk` 다.
 */

/** 판정에 필요한 텔레메트리 점. `TelemetryPoint` 의 부분집합이다. */
export interface RecorderSample {
  time: string;
  recorderOk: boolean | null;
  recorderLastFailure: string | null;
}

/**
 * 마감 창의 길이. 마감은 POST_RECORDING 3초 뒤에 시작해 MP4 muxing 몇 초로 끝나고
 * 텔레메트리 버킷은 10초라, 2분이면 결과가 반드시 들어온다. 창을 두는 이유는 창이
 * 없으면 **이 발견 이후의 다른 마감 결과**까지 끌어와 엉뚱한 사유를 붙이기 때문이다.
 */
export const RECORDER_SETTLE_SEC = 120;

export type MissingMediaKind =
  /** 마감이 실패했다. 영상은 다시 생기지 않는다 — 젯슨 pending 을 봐야 한다. */
  | "RECORDING_FAILED"
  /** 마감은 됐고 업로드가 아직이다. 기다리면 생긴다. */
  | "UPLOAD_PENDING"
  /** 녹화기 판정값이 없다. 「정상」이 아니라 모른다는 뜻이다. */
  | "NO_EVIDENCE";

export interface MissingMediaVerdict {
  kind: MissingMediaKind;
  /** 화면에 그대로 쓰는 한 줄. */
  message: string;
  /**
   * 젯슨 `report.json` 의 `mediaState` 원문. 실패가 아니면 null.
   * 한국어 문구는 표시 계층 변환일 뿐이고 대조는 원문으로 한다 — 툴팁에 남긴다.
   */
  rawReason: string | null;
}

/**
 * 관측 가능한 `mediaState` 값. 열거형으로 고정하지 않는다 — 젯슨이
 * `RECORDING_FAILED_{사유}` 로 만들어 늘어날 수 있고, 모르는 값이 오면 원문을
 * 그대로 보여 주는 것이 화면이 거짓말하지 않는 유일한 방법이다.
 */
export const MEDIA_STATE_LABEL: Record<string, string> = {
  RECORDING_FAILED_PTS_REGRESSION: "조각 타임스탬프 역행으로 영상 생성 실패",
  RECORDING_FAILED_NO_SEGMENTS: "녹화 조각이 없어 영상 생성 실패",
  RECORDING_FAILED_DISK_FULL: "저장 공간이 없어 녹화 실패",
  RECORDING_FAILED_UNEXPECTED: "예기치 못한 오류로 마감 실패",
  CORRUPT: "파일이 손상돼 폐기 — 부팅 복구가 찾은 지난 기동의 잔해",
};

/** 사유 원문을 사람이 읽는 문구로. 모르는 값은 원문 그대로다. */
export function mediaStateLabel(raw: string): string {
  return MEDIA_STATE_LABEL[raw] ?? raw;
}

function toMillis(iso: string): number {
  return new Date(iso).getTime();
}

/**
 * 미디어가 없는 발견이 왜 없는지 판정한다.
 *
 * @param encounter 발견. 마감은 종료 뒤에 일어나므로 `endedAt` 을 기준으로 삼고,
 *   진행 중이라 없으면 `startedAt` 을 쓴다.
 * @param points 그 임무의 텔레메트리 점. 순서는 상관없다.
 * @param nextEncounterStartedAt 다음 발견의 시작 시각. 있으면 창의 끝을 여기로
 *   당긴다 — 발견이 촘촘하면 다음 발견의 마감 결과가 이 발견에 붙는다.
 */
export function classifyMissingMedia(
  encounter: { startedAt: string; endedAt: string | null },
  points: RecorderSample[],
  nextEncounterStartedAt?: string | null,
): MissingMediaVerdict {
  const pivot = toMillis(encounter.endedAt ?? encounter.startedAt);
  const settleEnd = pivot + RECORDER_SETTLE_SEC * 1000;
  const nextStart = nextEncounterStartedAt ? toMillis(nextEncounterStartedAt) : Number.POSITIVE_INFINITY;
  const windowEnd = Math.min(settleEnd, nextStart);

  // 버킷 시각은 구간의 시작이라, 발견 시각을 걸친 버킷은 발견 **이전** 표본까지
  // 섞고 있다(bool_and). 그 버킷을 버리는 쪽이 안전하다 — 잃는 것은 버킷 하나
  // 만큼의 지연이고, 얻는 것은 이전 마감 결과를 이 발견에 붙이지 않는 것이다.
  const window = points
    .filter(p => {
      const t = toMillis(p.time);
      return t >= pivot && t <= windowEnd;
    })
    .sort((a, b) => toMillis(a.time) - toMillis(b.time));

  // 가장 늦은 **판정값**. 마감 직후 창의 앞쪽에는 아직 직전 마감의 결과가 실려
  // 있을 수 있어서 첫 점이 아니라 마지막 점을 본다.
  let settled: RecorderSample | null = null;
  for (const p of window) {
    if (p.recorderOk !== null) settled = p;
  }

  if (settled === null) {
    return {
      kind: "NO_EVIDENCE",
      message: "녹화기 상태 보고가 없어 영상이 없는 이유를 판정할 수 없습니다",
      rawReason: null,
    };
  }

  if (settled.recorderOk) {
    // 사유가 함께 와도 실패가 아니다 — 래치된 이전 실패다. 여기서 실패로 그리면
    // 마감이 정상인 발견이 전부 실패로 보인다.
    return {
      kind: "UPLOAD_PENDING",
      message: "마감은 정상입니다 — 영상 업로드 전이니 잠시 후 다시 열어보세요",
      rawReason: null,
    };
  }

  // 마감이 실패했다. 사유는 그 시점에 래치된 값이며, 실패 판정과 사유가 같은
  // 텔레메트리에 실리지 않는 드문 경우(버킷 경계)를 위해 창 안 마지막 값까지 본다.
  let raw: string | null = null;
  for (const p of window) {
    if (p.recorderLastFailure !== null) raw = p.recorderLastFailure;
  }
  return {
    kind: "RECORDING_FAILED",
    message: raw
      ? `녹화 실패 — ${mediaStateLabel(raw)}. 이 발견의 영상은 생기지 않습니다`
      : "녹화 실패 — 사유가 보고되지 않았습니다. 이 발견의 영상은 생기지 않습니다",
    rawReason: raw,
  };
}

export type MissionRecorderState =
  /** 이 임무 창의 텔레메트리에 마감 실패 흔적이 있다. */
  | "FAILED"
  /** 마감이 정상이었고 실패 사유도 없다. */
  | "OK"
  /** 판정값이 없다. recorder 없이 돌았거나 마감한 이벤트가 없었다. */
  | "NO_EVIDENCE";

export interface MissionRecorderSummary {
  state: MissionRecorderState;
  /** 마지막 실패 사유 원문. 없으면 null. */
  lastFailure: string | null;
}

/**
 * 임무 하나의 녹화기 상태를 한 줄로. 발견을 하나하나 열지 않아도 「이 임무에 마감
 * 실패가 있었다」가 보여야 재발을 알아챌 수 있다.
 *
 * 사유는 래치되고 그 래치는 임무 경계에서 지워지지 않는다 — 같은 기동의 앞선 임무에서
 * 온 값이 이 임무 창에도 실려 올 수 있다. 그래서 문구는 「이 임무가 실패했다」가 아니라
 * 「실패 사유가 남아 있다」여야 한다. 시점을 좁히려면 발견별 판정
 * ({@link classifyMissingMedia})을 본다.
 */
export function summarizeMissionRecorder(points: RecorderSample[]): MissionRecorderSummary {
  let lastFailure: string | null = null;
  let lastFailureAt = Number.NEGATIVE_INFINITY;
  let sawFalse = false;
  let sawTrue = false;
  for (const p of points) {
    // 서버는 버킷 순서로 주지만 여기서 순서에 기대지 않는다 — 「마지막 사유」가
    // 배열 순서로 결정되면 호출부가 정렬을 바꾸는 날 조용히 틀린 사유를 띄운다.
    if (p.recorderLastFailure !== null && toMillis(p.time) >= lastFailureAt) {
      lastFailure = p.recorderLastFailure;
      lastFailureAt = toMillis(p.time);
    }
    if (p.recorderOk === false) sawFalse = true;
    if (p.recorderOk === true) sawTrue = true;
  }
  if (sawFalse || lastFailure !== null) return { state: "FAILED", lastFailure };
  if (sawTrue) return { state: "OK", lastFailure: null };
  return { state: "NO_EVIDENCE", lastFailure: null };
}

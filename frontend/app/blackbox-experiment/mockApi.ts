/**
 * 백엔드·S3 없이 관제 블랙박스 화면을 띄우기 위한 목 데이터 (S15P11A301-202 실험).
 *
 * 실제 `lib/api.ts`와 같은 타입을 돌려주므로, 실험 페이지는 import 한 줄만 다르고
 * 나머지는 `app/blackbox/page.tsx`와 같다. 토글 기능 추가분만 diff 로 보인다.
 *
 * 미디어 파일은 **커밋하지 않는다.** 실육성이 들어간 샘플이라 개인 음성 커밋 금지
 * 규정(docs/08-AI-음성.md 33.5)에 걸린다. 아래 경로에 직접 두고 실행한다.
 *
 *   frontend/public/experiment/event_sample.mp4           원본 (오디오 트랙 포함)
 *   frontend/public/experiment/event_sample-denoised.m4a  잡음 제거본
 *
 * 만드는 방법:
 *   python denoise_try/make_demo_event.py                 샘플 이벤트 영상 생성
 *   python ai/voice/denoise/enhance_media.py <위 파일>           제거본 생성
 */

import type {
  EncounterDetail,
  EncounterSummary,
  MissionSummary,
  PresignedUrl,
  TelemetryPoint,
} from "@/lib/api";

/** 관제가 실제로 붙일 미디어 종류. 백엔드 enum 확장이 선행돼야 한다. */
export const DENOISED_AUDIO_TYPE = "EVENT_AUDIO_DENOISED";

const MISSION_ID = "11111111-1111-4111-8111-111111111111";
const ENCOUNTER_ID = "22222222-2222-4222-8222-222222222222";
const VIDEO_MEDIA_ID = "33333333-3333-4333-8333-333333333333";
const AUDIO_MEDIA_ID = "44444444-4444-4444-8444-444444444444";

const MISSIONS: MissionSummary[] = [
  {
    id: MISSION_ID,
    robotId: "sentinel-01",
    status: "COMPLETED",
    startedAt: "2026-08-03T04:10:00.000Z",
    endedAt: "2026-08-03T04:31:00.000Z",
    endReason: "COMPLETED",
    createdAt: "2026-08-03T04:09:30.000Z",
    durationSec: 1260,
    distanceM: 84.3,
    detectionCount: 1,
  },
];

const ENCOUNTERS: EncounterSummary[] = [
  {
    id: ENCOUNTER_ID,
    status: "ENDED",
    mapX: 12.4,
    mapY: -3.8,
    mapYaw: 1.57,
    detectedPersonCount: 3,
    startedAt: "2026-08-03T04:18:22.000Z",
    endedAt: "2026-08-03T04:19:05.000Z",
    terminationReason: "REPORT_SENT",
  },
];

const DETAIL: EncounterDetail = {
  id: ENCOUNTER_ID,
  missionId: MISSION_ID,
  status: "ENDED",
  mapX: 12.4,
  mapY: -3.8,
  mapYaw: 1.57,
  detectedPersonCount: 3,
  responsivePersonCount: 1,
  unresponsivePersonCount: 2,
  interactionSummary: "다친 곳 없음 · 이동 가능 · 주변 3명",
  encounterPose: {
    x: 12.4,
    y: -3.8,
    yaw: 1.57,
    mapId: "floor-1",
  },
  additionalPersonReports: [
    {
      subjectText: "우리 아기",
      reportedCount: 1,
      countStatus: "EXACT",
      locationText: "2층",
      reportedFloor: 2,
      groundingStatus: "UNGROUNDED",
      responseStatus: "UNKNOWN",
      certaintyStatus: "ASSERTED",
      rawUtterance: "2층에 우리 아기가 있어요",
      verificationStatus: "UNVERIFIED",
      operatorReviewRequired: true,
    },
  ],
  startedAt: "2026-08-03T04:18:22.000Z",
  interactionStartedAt: "2026-08-03T04:18:30.000Z",
  interactionEndedAt: "2026-08-03T04:19:01.000Z",
  endedAt: "2026-08-03T04:19:05.000Z",
  terminationReason: "REPORT_SENT",
  media: [
    {
      mediaId: VIDEO_MEDIA_ID,
      type: "EVENT_VIDEO",
      storageStatus: "AVAILABLE",
      durationMs: 30621,
    },
    // 202가 추가하는 자산. 원본 오디오에서 파생되므로 타임라인이 일치한다.
    {
      mediaId: AUDIO_MEDIA_ID,
      type: DENOISED_AUDIO_TYPE,
      storageStatus: "AVAILABLE",
      durationMs: 30656,
    },
  ],
};

const VIEW_URLS: Record<string, string> = {
  [VIDEO_MEDIA_ID]: "/experiment/event_sample.mp4",
  [AUDIO_MEDIA_ID]: "/experiment/event_sample-denoised.m4a",
};

/** 그래프가 비지 않도록 톱니 모양 CPU 사용률을 만든다. */
const TELEMETRY: TelemetryPoint[] = Array.from({ length: 40 }, (_, i) => ({
  time: new Date(Date.parse("2026-08-03T04:10:00.000Z") + i * 30_000).toISOString(),
  cpu: 38 + 22 * Math.abs(Math.sin(i / 4)),
  gpu: 25 + 15 * Math.abs(Math.cos(i / 5)),
  memory: 52 + 6 * Math.abs(Math.sin(i / 7)),
  jetsonTemp: 48 + 4 * Math.abs(Math.cos(i / 6)),
  battery: 100 - i * 0.8,
  // #205 추가 필드 — 이 실험판 그래프는 안 쓰므로 결측으로 둔다.
  temperature: null,
  humidity: null,
  linearVelocity: null,
  angularVelocity: null,
  mcuConnected: null,
  // #310 추가 필드. null 은 「정상」이 아니라 「판정 근거 없음」이며, 실험판에는
  // 녹화기 보고가 없으므로 그것이 맞다.
  recorderOk: null,
  recorderLastFailure: null,
}));

const delay = <T,>(value: T, ms = 120): Promise<T> =>
  new Promise(resolve => setTimeout(() => resolve(value), ms));

/** `lib/api.ts`의 `api` 중 블랙박스 화면이 쓰는 것만 흉내낸다. */
export const mockApi = {
  missions: () => delay(MISSIONS),
  missionEncounters: (_missionId: string) => delay(ENCOUNTERS),
  encounterDetail: (_encounterId: string) => delay(DETAIL),
  missionTelemetry: (_missionId: string, _limit: number) => delay(TELEMETRY),
  mediaViewUrl: (mediaId: string): Promise<PresignedUrl> =>
    delay({
      objectKey: VIEW_URLS[mediaId] ?? "",
      url: VIEW_URLS[mediaId] ?? "",
      expiresInSec: 600,
    }),
};

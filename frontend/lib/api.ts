/**
 * 백엔드 REST 클라이언트 (명세 27.4).
 *
 * 모든 응답은 ApiResponse{ data, message, status } 봉투로 오므로 여기서 언래핑한다.
 * 실패(4xx·5xx)는 서버의 status 코드(MISSION-001 등)와 메시지를 담은 ApiError 로 던진다.
 */

// 환경변수가 비어 있을 때의 안전망: localhost 에서 열렸으면 로컬 백엔드,
// 그 외(Vercel 배포·커스텀 도메인)에서는 운영 API 로 간다. Vercel 프로젝트에
// NEXT_PUBLIC_API_BASE_URL 설정이 누락돼도 배포본이 동작해야 한다.
const FALLBACK_BASE =
  typeof window !== "undefined" && !window.location.hostname.startsWith("localhost")
    ? "https://api.sentinel-ugv.xyz"
    : "http://localhost:8080";

// .env.example 이 한때 `/api` 접미사를 포함했어서 어느 형태의 값이 와도 동작하게 정규화한다.
const RAW_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? FALLBACK_BASE;
export const API_BASE = RAW_BASE.replace(/\/api\/?$/, "").replace(/\/+$/, "");

interface ApiEnvelope<T> {
  data: T;
  message: string;
  status: string;
}

export class ApiError extends Error {
  constructor(
    readonly httpStatus: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  let body: ApiEnvelope<T>;
  try {
    body = (await res.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiError(res.status, "PARSE", `응답을 해석할 수 없습니다 (HTTP ${res.status})`);
  }
  if (!res.ok) throw new ApiError(res.status, body.status, body.message);
  return body.data;
}

// ── 서버 DTO (backend dto record 와 1:1) ────────────────────────────────────

export interface MissionSummary {
  id: string;
  robotId: string;
  status: string;
  startedAt: string | null;
  endedAt: string | null;
  endReason: string | null;
  createdAt: string;
  durationSec: number | null;
  distanceM: number | null;
  detectionCount: number | null;
}

export interface MissionDetail extends MissionSummary {
  homePose: unknown | null;
  coverage: number | null;
}

export interface EncounterSummary {
  id: string;
  status: string;
  mapX: number | null;
  mapY: number | null;
  mapYaw: number | null;
  detectedPersonCount: number | null;
  startedAt: string;
  endedAt: string | null;
  terminationReason: string | null;
}

export interface EncounterMedia {
  mediaId: string;
  type: string;
  storageStatus: string;
  durationMs: number | null;
}

export interface EncounterPose {
  x: number;
  y: number;
  yaw: number;
  mapId: string | null;
}

export interface AdditionalPersonReport {
  subjectText: string | null;
  reportedCount: number | null;
  countStatus: "EXACT" | "PRESENCE_CONFIRMED_COUNT_UNKNOWN";
  locationText: string | null;
  reportedFloor: number | null;
  groundingStatus: "UNGROUNDED";
  responseStatus: "RESPONSIVE" | "UNRESPONSIVE" | "UNKNOWN";
  certaintyStatus: "ASSERTED" | "TENTATIVE";
  rawUtterance: string;
  verificationStatus: "UNVERIFIED";
  operatorReviewRequired: boolean;
}

export interface EncounterDetail {
  id: string;
  missionId: string;
  status: string;
  mapX: number | null;
  mapY: number | null;
  mapYaw: number | null;
  detectedPersonCount: number | null;
  responsivePersonCount: number | null;
  unresponsivePersonCount: number | null;
  interactionSummary: string | null;
  encounterPose: EncounterPose | null;
  additionalPersonReports: AdditionalPersonReport[];
  startedAt: string;
  interactionStartedAt: string | null;
  interactionEndedAt: string | null;
  endedAt: string | null;
  terminationReason: string | null;
  media: EncounterMedia[];
}

export interface PresignedUrl {
  objectKey: string;
  url: string;
  expiresInSec: number;
}

export interface TelemetryPoint {
  time: string;
  cpu: number | null;
  gpu: number | null;
  memory: number | null;
  jetsonTemp: number | null;
  battery: number | null;
  /** 이하 #205 — null 은 그 구간에 측정값이 없었다는 뜻(결측)이고 0 과 다르다. */
  temperature: number | null;
  humidity: number | null;
  linearVelocity: number | null;
  angularVelocity: number | null;
  /** 구간 bool_and — 한 번이라도 끊겼으면 false, 보고가 없었으면 null. */
  mcuConnected: boolean | null;
}

/** 임무 무관 최신 센서 값 (#255). 시각이 그룹별로 따로 온다 — 신선도 판정은 화면 몫. */
export interface TelemetryLatest {
  environmentTime: string | null;
  temperature: number | null;
  humidity: number | null;
  mcuTime: string | null;
  mcuConnected: boolean | null;
  /** 주행 지표 (#300). robot_pose 에서 오므로 시각이 또 따로다 — 신선도는 poseTime 으로 본다. */
  poseTime: string | null;
  linearVelocity: number | null;
  angularVelocity: number | null;
}

export interface MapView {
  mapId: string;
  pgmUrl: string;
  yamlUrl: string;
  expiresInSec: number;
  /** 전정밀 메타데이터 (#197). 완료 이전·구버전 지도는 null — 그때는 yamlUrl 파싱 폴백. */
  resolution: number | null;
  originX: number | null;
  originY: number | null;
  originYaw: number | null;
  width: number | null;
  height: number | null;
}

export interface TrajectoryPoint {
  time: string;
  x: number;
  y: number;
  yaw: number;
}

export interface Trajectory {
  /** 이 궤적과 같은 좌표계인 지도. 지도 등록 전엔 null. */
  mapId: string | null;
  points: TrajectoryPoint[];
}

export type CommandType = "START" | "PAUSE" | "RESUME" | "RETURN" | "STOP";

export interface CommandResponse {
  commandId: string;
  status: string;
}

/** 명령 처리 결과 (#207). result: PENDING → ACCEPTED/EXECUTED 또는 REJECTED/EXPIRED/FAILED. */
export interface CommandStatus {
  commandId: string;
  type: string;
  result: string;
  /** 거부·실패 사유 (ROBOT_BUSY 등). 성공·대기면 null. */
  reasonCode: string | null;
  requestedAt: string;
}

// ── 엔드포인트 ──────────────────────────────────────────────────────────────

export const api = {
  missions: () => request<MissionSummary[]>("/api/v1/missions"),

  missionDetail: (missionId: string) =>
    request<MissionDetail>(`/api/v1/missions/${missionId}`),

  createMission: (robotId: string) =>
    request<MissionDetail>("/api/v1/missions", {
      method: "POST",
      body: JSON.stringify({ robotId }),
    }),

  issueCommand: (missionId: string, type: CommandType) =>
    request<CommandResponse>(`/api/v1/missions/${missionId}/commands`, {
      method: "POST",
      body: JSON.stringify({ type }),
    }),

  missionCommands: (missionId: string) =>
    request<CommandStatus[]>(`/api/v1/missions/${missionId}/commands`),

  missionEncounters: (missionId: string) =>
    request<EncounterSummary[]>(`/api/v1/missions/${missionId}/encounters`),

  encounterDetail: (encounterId: string) =>
    request<EncounterDetail>(`/api/v1/encounters/${encounterId}`),

  mediaViewUrl: (mediaId: string) =>
    request<PresignedUrl>(`/api/v1/media/${mediaId}/view-url`),

  missionMap: (missionId: string) =>
    request<MapView>(`/api/v1/missions/${missionId}/map`),

  missionTrajectory: (missionId: string, maxPoints?: number) =>
    request<Trajectory>(
      `/api/v1/missions/${missionId}/trajectory${maxPoints ? `?maxPoints=${maxPoints}` : ""}`,
    ),

  telemetryLatest: () => request<TelemetryLatest>("/api/v1/telemetry/latest"),

  missionTelemetry: (missionId: string, bucketSeconds = 10, from?: string) =>
    request<TelemetryPoint[]>(
      `/api/v1/missions/${missionId}/telemetry?bucketSeconds=${bucketSeconds}`
        + (from ? `&from=${encodeURIComponent(from)}` : ""),
    ),
};

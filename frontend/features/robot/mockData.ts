// 목 데이터 생성기 — RobotContext 의 USE_MOCK 을 끄면 실 백엔드를 쓴다.
//
// 목업 점유 격자(buildMockGrid·revealArea·GRID_SIZE)와 순찰 경로(PATROL_PATH)를
// 뺐다 (S15P11A301-227). 메인 지도가 실시간 SLAM 지도로 바뀌어 읽는 곳이
// 없어졌다. DetectionEvent 의 gridR·gridC 도 같이 뺐다 — 목업 격자 인덱스라
// 실제 map 좌표(mapX·mapY)와 단위가 달랐다.

// Deterministic PRNG
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

export interface SensorReading {
  temperature: number;
  humidity: number;
  battery: number;
  co2: number;
  timestamp: number;
}

export interface DetectionEvent {
  id: string;
  timestamp: number;
  confidence: number;
  thumbnailColor: string;
  location: string;
}

/**
 * 임무 상태 12개. 명세 26.2에서 확정된 값이며 common/schemas/state.schema.json의
 * `missionState`와 동일한 문자열을 쓴다. 화면 표시용으로 줄이거나 이름을 바꾸면
 * 백엔드가 보내는 값을 그대로 렌더할 수 없게 된다.
 */
export type MissionState =
  | "SAFE_IDLE"
  | "EXPLORING"
  | "PERSON_APPROACHING"
  | "INTERACTING"
  | "POST_RECORDING"
  | "REPORTING"
  | "PAUSED"
  | "MANUAL"
  | "RETURNING"
  | "COMPLETED"
  | "ESTOP"
  | "ERROR";

/** state.schema.json `controlMode`. 전환은 항상 PAUSED를 경유한다(26.3). */
export type ControlMode = "MANUAL" | "AUTO" | null;

/** state.schema.json `safetyState`. */
export type SafetyState =
  | "SAFE_IDLE"
  | "READY"
  | "RUNNING"
  | "STOPPED"
  | "ESTOP"
  | "FAULT"
  | null;

/**
 * telemetry.schema.json `health`. 3상태다.
 * true=확인됨, false=확인했고 끊김, null=확인할 수단이 없음.
 * false와 null을 같이 표시하면 "연동 전"과 "고장"을 구분할 수 없다.
 */
export interface ComponentHealth {
  mcuConnected: boolean | null;
  lidarOk: boolean | null;
  cameraOk: boolean | null;
}

/** 탐사를 종료시키는 배터리 임계값(명세 23.4). */
export const BATTERY_ABORT_PCT = 20;

export interface RobotStatus {
  connected: boolean;
  missionState: MissionState;
  controlMode: ControlMode;
  safetyState: SafetyState;
  health: ComponentHealth;
  speed: number;
  heading: number;
  uptime: number;
  errorCount: number;
  warningCount: number;
  infoCount: number;
}

export const INITIAL_SENSORS: SensorReading = {
  temperature: 28.4,
  humidity: 67.2,
  battery: 84,
  co2: 412,
  timestamp: Date.now(),
};


// Seeded PRNG so module-eval values are identical on server and client (avoids
// Next.js hydration mismatches; the timestamps are pinned to the hour below).
const bbRand = mulberry32(20240724);

export const MOCK_BLACKBOX_ENTRIES = Array.from({ length: 12 }, (_, i) => {
  const d = new Date();
  d.setDate(d.getDate() - Math.floor(i / 3));
  d.setHours(8 + (i % 3) * 4, 0, 0, 0);
  return {
    id: `bb-${i}`,
    date: d.toISOString().split("T")[0],
    startTime: d.toISOString(),
    duration: Math.floor(20 + bbRand() * 80),
    detections: Math.floor(bbRand() * 8),
    size: `${(0.4 + bbRand() * 2).toFixed(1)} GB`,
    thumbnailHue: 140 + i * 15,
  };
});

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

// 실측 전환(S15P11A301-205): null 은 "모름"이고 0 은 값이다(젯슨 계약).
// battery·co2 는 화면에서 이미 뺐고(#200) 계측 수단도 없어 타입에서도 제거했다.
export interface SensorReading {
  temperature: number | null;
  humidity: number | null;
  /** ESP32 연결 상태 — 온습도가 비었을 때 "보드 문제 vs 센서 문제"를 가른다. */
  mcuConnected: boolean | null;
  /** 마지막 실측 수신 시각(ms). null 이면 아직 받은 값이 없다. */
  updatedAt: number | null;
}

/**
 * 주행 지표 (S15P11A301-300). 후륜 MT6701 엔코더 2개를 ESP32 가 계수하고 젯슨이
 * 역산한 오도메트리다 — 라이다가 아니다(`message_mapper.py`, nav_msgs/Odometry 의 twist).
 *
 * 그래서 센서 보드가 빠지면 온습도와 함께 빈다. 라이다 SLAM 이 추정하는 위치(x·y·yaw)는
 * 그때도 오므로, "지도에서는 움직이는데 속도는 —" 이 정상 조합이다.
 */
export interface MotionReading {
  linearVelocity: number | null;
  angularVelocity: number | null;
  /** 마지막 수신 시각(ms). null 이면 아직 받은 값이 없다. */
  updatedAt: number | null;
}

export const INITIAL_MOTION: MotionReading = {
  linearVelocity: null, angularVelocity: null, updatedAt: null,
};

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
  /** 센서 ESP32(엔코더 발행자). 모터 보드와 **다른 보드**다. */
  mcuConnected: boolean | null;
  lidarOk: boolean | null;
  cameraOk: boolean | null;
  /**
   * 모터 ESP32 링크 (S15P11A301-317). 보드가 둘이라 값도 둘이다 — 2026-08-06
   * 실기동에서 모터 보드만 죽었을 때 화면이 그것을 말할 값이 없었고, 조작자는
   * 명령을 눌러 거부 알림을 받고서야 알았다.
   */
  motorLinkOk: boolean | null;
}

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

// 시작값은 전부 "모름" — 실측이 오기 전에 그럴싸한 숫자를 보여주지 않는다(#205).
export const INITIAL_SENSORS: SensorReading = {
  temperature: null,
  humidity: null,
  mcuConnected: null,
  updatedAt: null,
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

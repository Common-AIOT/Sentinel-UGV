"use client";

import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import {
  buildMockGrid,
  revealArea,
  PATROL_PATH,
  GRID_SIZE,
  INITIAL_SENSORS,
  DETECTION_LOCATIONS,
  EXPLORATION_LIMIT_SEC,
  BATTERY_ABORT_PCT,
  type SensorReading,
  type DetectionEvent,
  type RobotStatus,
  type MissionState,
} from "./mockData";

import { api, ApiError, type CommandType } from "@/lib/api";

// 명령·조회는 실 API(@/lib/api)를 쓴다. 지도 격자와 환경 센서는 서버 계약이 아직
// 없으므로(S15P11A301-169 결정) 목 시뮬레이션을 유지한다.
export const USE_MOCK = true;

/** 확정 로봇 이름 (mqtt-setup·mvp-week-plan). */
const ROBOT_ID = "SENTINEL-01";

/** 서버 missions.status → 관제 화면 MissionState. 서버는 5개 상태만 가진다. */
const SERVER_MISSION_STATE: Record<string, MissionState> = {
  CREATED: "SAFE_IDLE",
  EXPLORING: "EXPLORING",
  PAUSED: "PAUSED",
  RETURNING: "RETURNING",
  COMPLETED: "COMPLETED",
};

// ── Types ──────────────────────────────────────────────────────────────────
export interface RobotPos { r: number; c: number; heading: number; }

interface RobotContextValue {
  // Map
  grid: number[][];
  robotPos: RobotPos;
  pathHistory: RobotPos[];
  // Status
  status: RobotStatus;
  // Sensors
  sensors: SensorReading;
  // Detections
  detections: DetectionEvent[];
  activeDetection: DetectionEvent | null;
  dismissDetection: () => void;
  // Control
  sendControl: (x: number, y: number) => void;
  sendCommand: (type: string) => Promise<void>;
  tagEvent: () => void;
  // Mission
  missionId: string | null;
  // Video
  videoConnected: boolean;
  videoQuality: "1080p" | "720p";
  setVideoQuality: (q: "1080p" | "720p") => void;
  // WS connection
  wsConnected: boolean;
}

const RobotCtx = createContext<RobotContextValue | null>(null);

export function RobotProvider({ children }: { children: React.ReactNode }) {
  const [grid, setGrid] = useState<number[][]>(() => buildMockGrid());
  const [robotPos, setRobotPos] = useState<RobotPos>({ r: 10, c: 10, heading: 0 });
  const [pathHistory, setPathHistory] = useState<RobotPos[]>([{ r: 10, c: 10, heading: 0 }]);
  const [sensors, setSensors] = useState<SensorReading>(INITIAL_SENSORS);
  const [detections, setDetections] = useState<DetectionEvent[]>([]);
  const [activeDetection, setActiveDetection] = useState<DetectionEvent | null>(null);
  const [videoConnected, setVideoConnected] = useState(false);
  const [videoQuality, setVideoQuality] = useState<"1080p" | "720p">("1080p");
  const [wsConnected, setWsConnected] = useState(false);
  const [status, setStatus] = useState<RobotStatus>({
    connected: false,
    missionState: "SAFE_IDLE",
    controlMode: null,
    safetyState: "SAFE_IDLE",
    // 연동 전에는 null이 정직한 값이다. false로 두면 "고장"으로 읽힌다.
    health: { mcuConnected: null, lidarOk: null, cameraOk: null },
    speed: 0,
    heading: 0,
    uptime: 0,
    explorationElapsedSec: 0,
    explorationLimitSec: EXPLORATION_LIMIT_SEC,
    errorCount: 0,
    warningCount: 2,
    infoCount: 7,
  });

  // Mock patrol state
  const waypointIdx = useRef(0);
  const mockMission = useRef<MissionState>("SAFE_IDLE");
  const startTime = useRef(Date.now());
  const exploreStart = useRef<number | null>(null);
  // 탐사 경과는 "탐사 계열 상태였던 초"의 누적이다 — 일시정지 동안은 흐르지 않는다.
  const exploreElapsedSec = useRef(0);

  // 실 임무 상태. missionId 가 있으면 임무 상태는 서버 폴링이 결정하고,
  // 목 시뮬레이션의 가짜 탐지·자동 전이는 멈춘다(지도 주행 애니메이션만 유지).
  const [missionId, setMissionId] = useState<string | null>(null);
  const missionIdRef = useRef<string | null>(null);
  const robotPosRef = useRef(robotPos);
  useEffect(() => { robotPosRef.current = robotPos; }, [robotPos]);

  // 명령 직후 유예 창. 202 접수 후 ACK 가 서버에 반영되기 전에 폴링이 옛 상태를
  // 가져와 낙관적 표시("복귀 중")를 "탐사 중"으로 되돌리는 경합을 막는다.
  // 유예 안에 "명령 이전과 같은 상태"가 오면 무시하고, 새 상태는 즉시 반영한다.
  const pendingCommand = useRef<{ before: MissionState; at: number } | null>(null);
  const COMMAND_GRACE_MS = 10_000;

  // ── Mock simulation ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!USE_MOCK) return;

    // Simulate connection after 1s.
    // 명세 26.3: 초기화가 끝나면 SAFE_IDLE에서 운영자의 명시적 시작을 기다린다.
    // 접속만으로 탐사가 시작되면 안 된다.
    const connectTimer = setTimeout(() => {
      setWsConnected(true);
      setVideoConnected(true);
      setStatus(s => ({
        ...s,
        connected: true,
        // 새로고침 복구가 먼저 활성 임무를 이어받았으면 그 상태를 덮지 않는다.
        missionState: missionIdRef.current ? s.missionState : "SAFE_IDLE",
        safetyState: "READY",
        health: { mcuConnected: true, lidarOk: true, cameraOk: true },
      }));
      if (!missionIdRef.current) mockMission.current = "SAFE_IDLE";
    }, 1000);

    // Robot movement — 10Hz. 주행하는 상태만 좌표를 갱신한다.
    const moveTimer = setInterval(() => {
      const ms = mockMission.current;
      if (ms !== "EXPLORING" && ms !== "RETURNING" && ms !== "PERSON_APPROACHING") return;

      setRobotPos(prev => {
        const target = PATROL_PATH[waypointIdx.current % PATROL_PATH.length];
        const dr = target.r - prev.r;
        const dc = target.c - prev.c;
        const dist = Math.sqrt(dr * dr + dc * dc);

        if (dist < 1.5) {
          waypointIdx.current = (waypointIdx.current + 1) % PATROL_PATH.length;
          return prev;
        }

        const speed = 0.4;
        const nr = prev.r + (dr / dist) * speed;
        const nc = prev.c + (dc / dist) * speed;
        const heading = Math.atan2(dc, dr) * (180 / Math.PI);

        const nr2 = Math.round(nr);
        const nc2 = Math.round(nc);
        setGrid(g => {
          const ng = g.map(row => [...row]);
          revealArea(ng, nr2, nc2, 14);
          return ng;
        });

        const newPos = { r: nr, c: nc, heading };
        setPathHistory(h => [...h.slice(-200), newPos]);

        setStatus(s => ({
          ...s,
          // 명세 24: 자율 0.25m/s, 사람 접근 0.10m/s
          speed:
            mockMission.current === "PERSON_APPROACHING"
              ? 0.10
              : 0.25 + Math.random() * 0.03,
          heading: Math.round(heading),
          uptime: Math.floor((Date.now() - startTime.current) / 1000),
        }));

        return newPos;
      });
    }, 100);

    // 탐사 경과·종료 조건 — 1Hz.
    // 명세 23.4의 두 종료 조건(제한 시간, 배터리 20%)을 모의한다.
    const missionTimer = setInterval(() => {
      setStatus(s => {
        if (exploreStart.current === null) return s;
        // 탐사 계열 상태에서만 1초씩 누적한다. PAUSED·MANUAL·RETURNING 은 흐르지 않는다.
        const exploring =
          mockMission.current === "EXPLORING" ||
          mockMission.current === "PERSON_APPROACHING";
        if (exploring) exploreElapsedSec.current += 1;
        const elapsed = exploreElapsedSec.current;
        if (exploring && elapsed >= s.explorationLimitSec && !missionIdRef.current) {
          // 실 임무 중에는 종료 조건도 서버·로봇의 판단을 따른다.
          mockMission.current = "RETURNING";
          return { ...s, explorationElapsedSec: elapsed, missionState: "RETURNING" };
        }
        // 주행하지 않는 단계에서는 속도가 0으로 읽혀야 한다. 명세 26.2는
        // INTERACTING을 "피해자 음성 확인, 정지"로 규정한다. 마지막 주행 속도가
        // 남아 있으면 정지한 로봇이 움직이는 것으로 보인다.
        const moving =
          mockMission.current === "EXPLORING" ||
          mockMission.current === "RETURNING" ||
          mockMission.current === "PERSON_APPROACHING";
        return {
          ...s,
          explorationElapsedSec: elapsed,
          speed: moving ? s.speed : 0,
        };
      });
    }, 1000);

    // Sensor updates — 2s
    const sensorTimer = setInterval(() => {
      setSensors(s => {
        const next = {
          temperature: +(s.temperature + (Math.random() - 0.5) * 0.4).toFixed(1),
          humidity: +(Math.max(30, Math.min(95, s.humidity + (Math.random() - 0.5) * 1.2))).toFixed(1),
          battery: +(Math.max(0, s.battery - 0.02)).toFixed(1),
          co2: Math.round(s.co2 + (Math.random() - 0.5) * 5),
          timestamp: Date.now(),
        };
        // 명세 23.4: 배터리 20% 이하는 탐사 종료 조건이다. (목 전용 — 실 임무는 로봇 판단)
        if (next.battery <= BATTERY_ABORT_PCT && mockMission.current === "EXPLORING" && !missionIdRef.current) {
          mockMission.current = "RETURNING";
          setStatus(st => ({ ...st, missionState: "RETURNING" }));
        }
        return next;
      });
    }, 2000);

    // Random detection events.
    // 명세 26.2의 encounter 시퀀스를 모의한다:
    // EXPLORING → PERSON_APPROACHING → INTERACTING → POST_RECORDING → REPORTING → EXPLORING
    // 관제 화면이 각 단계를 구분해 보여줄 수 있는지 확인하려면 단계가 실제로
    // 흘러야 한다. 한 번에 EXPLORING으로 돌아오면 검증할 수 없다.
    const encounterTimers: ReturnType<typeof setTimeout>[] = [];
    const runEncounter = () => {
      if (mockMission.current !== "EXPLORING") return;
      const advance = (to: MissionState, delay: number) =>
        encounterTimers.push(
          setTimeout(() => {
            // 도중에 E-Stop이나 수동 전환이 끼어들면 시퀀스를 포기한다.
            const ms = mockMission.current;
            const owned =
              ms === "PERSON_APPROACHING" || ms === "INTERACTING" ||
              ms === "POST_RECORDING" || ms === "REPORTING";
            if (!owned) return;
            mockMission.current = to;
            setStatus(s => ({ ...s, missionState: to }));
          }, delay),
        );

      mockMission.current = "PERSON_APPROACHING";
      setStatus(s => ({ ...s, missionState: "PERSON_APPROACHING" }));
      advance("INTERACTING", 4000);
      advance("POST_RECORDING", 12000);
      advance("REPORTING", 15000);
      advance("EXPLORING", 17000);
    };

    const detectionTimer = setInterval(() => {
      // 실 임무 중에는 가짜 탐지를 만들지 않는다 — 진짜 encounter 폴링이 대신한다.
      if (missionIdRef.current) return;
      if (Math.random() > 0.15) return;
      const event: DetectionEvent = {
        id: `det-${Date.now()}`,
        timestamp: Date.now(),
        confidence: +(0.72 + Math.random() * 0.26).toFixed(2),
        gridR: Math.floor(10 + Math.random() * 80),
        gridC: Math.floor(10 + Math.random() * 80),
        thumbnailColor: `hsl(${Math.floor(Math.random() * 30)}, 60%, 30%)`,
        location: DETECTION_LOCATIONS[Math.floor(Math.random() * DETECTION_LOCATIONS.length)],
      };
      setDetections(d => [event, ...d].slice(0, 20));
      setActiveDetection(event);
      setStatus(s => ({ ...s, warningCount: s.warningCount + 1 }));
      runEncounter();
    }, 8000);

    return () => {
      clearTimeout(connectTimer);
      clearInterval(moveTimer);
      clearInterval(missionTimer);
      clearInterval(sensorTimer);
      clearInterval(detectionTimer);
      encounterTimers.forEach(clearTimeout);
    };
  }, []);

  const dismissDetection = useCallback(() => setActiveDetection(null), []);

  const sendControl = useCallback((x: number, y: number) => {
    if (USE_MOCK) return; // mock: robot moves on its own
    // Real: send via STOMP to /app/control
    console.log("control", { x, y });
  }, []);

  /**
   * UI 명령 어휘 → 서버 명령(27.4) 매핑 후 발행. manual/auto 는 서버 명령이 없어
   * (제어 세션 범위, S15P11A301-39) 로컬 전이만 한다.
   *
   * explore 는 첫 호출에서 임무를 만들고 START, PAUSED 상태면 RESUME 을 보낸다.
   * 이미 활성 임무가 있어 생성이 409 면 그 임무를 이어받는다.
   * RETURN 은 젯슨이 NOT_IMPLEMENTED 로 거부하므로 데모에선 STOP 으로 대체한다(계획 결정).
   */
  const sendCommand = useCallback(async (type: string) => {
    if (type === "explore" || type === "return" || type === "pause") {
      let mid = missionIdRef.current;
      const resuming = type === "explore" && mockMission.current === "PAUSED";

      if (!mid && type === "explore") {
        try {
          const created = await api.createMission(ROBOT_ID);
          mid = created.id;
        } catch (e) {
          if (e instanceof ApiError && e.httpStatus === 409) {
            const active = (await api.missions()).find(m => m.endedAt === null);
            if (!active) throw e;
            mid = active.id;
          } else {
            throw e;
          }
        }
        missionIdRef.current = mid;
        setMissionId(mid);
      }

      if (mid) {
        const cmd: CommandType =
          type === "return" ? "STOP"
          : type === "pause" ? "PAUSE"
          : resuming ? "RESUME"
          : "START";
        pendingCommand.current = { before: mockMission.current, at: Date.now() };
        await api.issueCommand(mid, cmd);
      }
    }

    // 이하 낙관적 로컬 전이 — 202 접수 직후 화면이 즉시 반응하게 한다.
    // 실제 상태는 3초 폴링(아래 useEffect)이 서버 값으로 덮는다.
    // 명세 26.3의 전이 규칙을 모의한다. 임의 전이를 허용하면 관제 화면이
    // 실제 로봇에서는 불가능한 상태 조합을 보여주게 된다.
    setStatus(s => {
      const from = mockMission.current;
      let missionState: MissionState = from;
      let controlMode = s.controlMode;

      switch (type) {
        case "explore":
          // 재개는 운영자의 명시적 명령으로만 이루어진다(SR-008).
          if (from === "SAFE_IDLE" || from === "PAUSED" || from === "COMPLETED") {
            missionState = "EXPLORING";
            controlMode = "AUTO";
            if (exploreStart.current === null) {
              exploreStart.current = Date.now();
              exploreElapsedSec.current = 0; // 새 임무의 탐사 시계는 0부터
            }
          }
          break;

        case "return":
          if (from !== "ESTOP" && from !== "ERROR") {
            missionState = "RETURNING";
            controlMode = "AUTO";
            waypointIdx.current = 0;
          }
          break;

        case "manual":
          // 2단 전이. MANUAL 진입은 SAFE_IDLE 또는 PAUSED에서만 허용하므로
          // 주행 중이면 PAUSED를 자동 경유한다(26.3). 버튼 하나가 이걸 감춘다.
          if (from !== "ESTOP" && from !== "ERROR") {
            missionState = "MANUAL";
            controlMode = "MANUAL";
          }
          break;

        case "auto":
          // MANUAL 종료는 항상 PAUSED로 복귀한다. 자동 재출발 금지.
          if (from === "MANUAL") {
            missionState = "PAUSED";
            controlMode = "AUTO";
          }
          break;

        case "pause":
          if (from !== "ESTOP" && from !== "ERROR") missionState = "PAUSED";
          break;
      }

      mockMission.current = missionState;
      const stopped = missionState !== "EXPLORING" && missionState !== "RETURNING";
      return {
        ...s,
        missionState,
        controlMode,
        safetyState: stopped ? "STOPPED" : "RUNNING",
        speed: stopped ? 0 : s.speed,
      };
    });
  }, []);

  // ── 실 임무 폴링 ─────────────────────────────────────────────────────────
  // STOMP 가 프론트·백 모두 없어(S15P11A301-169) REST 폴링으로 대체한다.

  // 새로고침 복구 — missionId 는 리액트 상태에만 있으므로, 페이지 로드 시
  // 서버의 활성 임무(endedAt 없음)를 찾아 이어받는다. 없으면 목 초기 상태 그대로.
  useEffect(() => {
    (async () => {
      try {
        const active = (await api.missions()).find(m => m.endedAt === null);
        if (!active) return;
        missionIdRef.current = active.id;
        setMissionId(active.id);
        const mapped = SERVER_MISSION_STATE[active.status];
        if (mapped) {
          mockMission.current = mapped;
          setStatus(s => ({ ...s, missionState: mapped }));
        }
        // 잔여 탐사 시간 게이지도 서버의 실제 시작 시각으로 복원한다.
        // 서버는 일시정지 구간을 기록하지 않으므로 벽시계 근사값이다.
        if (active.startedAt) {
          exploreStart.current = Date.parse(active.startedAt);
          exploreElapsedSec.current = Math.max(
            0, Math.floor((Date.now() - Date.parse(active.startedAt)) / 1000));
        }
      } catch {
        // 서버에 못 붙으면 목 초기 상태로 남는다. 폴링이 아니므로 재시도하지 않는다.
      }
    })();
  }, []);

  // 임무 상태 3초 폴링 — 서버 상태가 로봇 ACK 의 결과이므로 이것이 진실이다.
  useEffect(() => {
    if (!missionId) return;
    const timer = setInterval(async () => {
      try {
        const m = await api.missionDetail(missionId);
        const mapped = SERVER_MISSION_STATE[m.status];
        if (!mapped) return;
        const pc = pendingCommand.current;
        if (pc && Date.now() - pc.at < COMMAND_GRACE_MS && mapped === pc.before) {
          // 아직 ACK 반영 전 — 낙관적 표시를 유지한다.
          return;
        }
        pendingCommand.current = null;
        mockMission.current = mapped;
        setStatus(s => ({
          ...s,
          missionState: mapped,
          // 완료된 임무의 탐사 시계가 계속 돌면 안 된다 — 게이지를 내린다.
          ...(mapped === "COMPLETED" ? { explorationElapsedSec: 0 } : {}),
        }));
        if (m.status === "COMPLETED") {
          // 다음 explore 가 새 임무를 만들 수 있게 활성 임무를 비우고 탐사 시계도 끈다.
          missionIdRef.current = null;
          setMissionId(null);
          exploreStart.current = null;
          exploreElapsedSec.current = 0;
        }
      } catch {
        // 일시적 네트워크 오류는 다음 폴링에 맡긴다.
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [missionId]);

  // encounter 5초 폴링 — 새 발견을 팝업·목록에 반영한다.
  // 좌표 계약(occupancy grid)이 없어 지도는 목이므로, 마커는 현재 로봇 위치에 찍는다.
  useEffect(() => {
    if (!missionId) return;
    const seen = new Set<string>();
    let first = true;
    const timer = setInterval(async () => {
      try {
        const list = await api.missionEncounters(missionId);
        const fresh = list.filter(e => !seen.has(e.id));
        fresh.forEach(e => seen.add(e.id));
        // 첫 폴링은 기존 발견을 조용히 채우고, 이후 새 발견만 팝업을 띄운다.
        const isFirst = first;
        first = false;
        if (fresh.length === 0) return;
        const events: DetectionEvent[] = fresh.map(e => ({
          id: e.id,
          timestamp: Date.parse(e.startedAt),
          confidence: 1,
          gridR: Math.round(robotPosRef.current.r),
          gridC: Math.round(robotPosRef.current.c),
          thumbnailColor: "hsl(20, 60%, 30%)",
          location:
            e.mapX !== null && e.mapY !== null
              ? `(${e.mapX.toFixed(1)}, ${e.mapY.toFixed(1)})`
              : "위치 미기록",
        }));
        setDetections(d => [...events, ...d].slice(0, 20));
        if (!isFirst) setActiveDetection(events[0]);
      } catch {
        // 다음 폴링에 맡긴다.
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [missionId]);

  const tagEvent = useCallback(() => {
    const evt: DetectionEvent = {
      id: `tag-${Date.now()}`,
      timestamp: Date.now(),
      confidence: 1.0,
      gridR: Math.round(robotPos.r),
      gridC: Math.round(robotPos.c),
      thumbnailColor: "hsl(220, 60%, 30%)",
      location: "Manual Tag — Operator",
    };
    setDetections(d => [evt, ...d].slice(0, 20));
    setStatus(s => ({ ...s, infoCount: s.infoCount + 1 }));
  }, [robotPos]);

  return (
    <RobotCtx.Provider value={{
      grid, robotPos, pathHistory,
      status, sensors,
      detections, activeDetection, dismissDetection,
      sendControl, sendCommand, tagEvent,
      missionId,
      videoConnected, videoQuality, setVideoQuality,
      wsConnected,
    }}>
      {children}
    </RobotCtx.Provider>
  );
}

export function useRobot() {
  const ctx = useContext(RobotCtx);
  if (!ctx) throw new Error("useRobot must be used inside RobotProvider");
  return ctx;
}

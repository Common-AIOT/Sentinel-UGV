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

// ── API endpoints ──────────────────────────────────────────────────────────
// 배포 환경마다 백엔드 주소가 다르므로 환경 변수로 주입한다. 값이 없으면 로컬 개발 기준으로
// 동작한다. Vercel 에서는 프로젝트 환경 변수에 NEXT_PUBLIC_API_BASE_URL 을 설정한다.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8080";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8080/ws";

export const API = {
  WS_CONTROL: `${WS_BASE}/control`,
  WS_SENSORS: `${WS_BASE}/sensors`,
  WS_DETECTIONS: `${WS_BASE}/detections`,
  WS_SIGNALING: `${WS_BASE}/signaling`,
  STOMP_BROKER: WS_BASE,
  CMD: (type: string) => `${API_BASE}/api/command/${type}`,
  BLACKBOX: `${API_BASE}/api/blackbox`,
};

export const USE_MOCK = true; // flip to false when backend is ready

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
        missionState: "SAFE_IDLE",
        safetyState: "READY",
        health: { mcuConnected: true, lidarOk: true, cameraOk: true },
      }));
      mockMission.current = "SAFE_IDLE";
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
        const elapsed = Math.floor((Date.now() - exploreStart.current) / 1000);
        const driving =
          mockMission.current === "EXPLORING" ||
          mockMission.current === "PERSON_APPROACHING";
        if (driving && elapsed >= s.explorationLimitSec) {
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
        // 명세 23.4: 배터리 20% 이하는 탐사 종료 조건이다.
        if (next.battery <= BATTERY_ABORT_PCT && mockMission.current === "EXPLORING") {
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

  const sendCommand = useCallback(async (type: string) => {
    if (!USE_MOCK) {
      await fetch(API.CMD(type), { method: "POST" });
      return;
    }

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
            if (exploreStart.current === null) exploreStart.current = Date.now();
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

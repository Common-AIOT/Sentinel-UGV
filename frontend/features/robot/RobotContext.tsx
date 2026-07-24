"use client";

import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import {
  buildMockGrid,
  revealArea,
  PATROL_PATH,
  GRID_SIZE,
  INITIAL_SENSORS,
  DETECTION_LOCATIONS,
  type SensorReading,
  type DetectionEvent,
  type RobotStatus,
} from "./mockData";

// ── API endpoints (swap these for real backend) ────────────────────────────
export const API = {
  WS_CONTROL: "ws://localhost:8080/ws/control",
  WS_SENSORS: "ws://localhost:8080/ws/sensors",
  WS_DETECTIONS: "ws://localhost:8080/ws/detections",
  WS_SIGNALING: "ws://localhost:8080/ws/signaling",
  STOMP_BROKER: "ws://localhost:8080/ws",
  CMD: (type: string) => `http://localhost:8080/api/command/${type}`,
  BLACKBOX: "http://localhost:8080/api/blackbox",
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
    mode: "IDLE",
    speed: 0,
    heading: 0,
    errorCount: 0,
    warningCount: 2,
    infoCount: 7,
    uptime: 0,
  });

  // Mock patrol state
  const waypointIdx = useRef(0);
  const mockMode = useRef<RobotStatus["mode"]>("IDLE");
  const startTime = useRef(Date.now());

  // ── Mock simulation ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!USE_MOCK) return;

    // Simulate connection after 1s
    const connectTimer = setTimeout(() => {
      setWsConnected(true);
      setVideoConnected(true);
      setStatus(s => ({ ...s, connected: true, mode: "EXPLORE" }));
      mockMode.current = "EXPLORE";
    }, 1000);

    // Robot movement — 10Hz
    const moveTimer = setInterval(() => {
      if (mockMode.current === "IDLE" || mockMode.current === "MANUAL") return;

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
          speed: 0.4 + Math.random() * 0.1,
          heading: Math.round(heading),
          uptime: Math.floor((Date.now() - startTime.current) / 1000),
        }));

        return newPos;
      });
    }, 100);

    // Sensor updates — 2s
    const sensorTimer = setInterval(() => {
      setSensors(s => ({
        temperature: +(s.temperature + (Math.random() - 0.5) * 0.4).toFixed(1),
        humidity: +(Math.max(30, Math.min(95, s.humidity + (Math.random() - 0.5) * 1.2))).toFixed(1),
        battery: +(Math.max(0, s.battery - 0.02)).toFixed(1),
        co2: Math.round(s.co2 + (Math.random() - 0.5) * 5),
        timestamp: Date.now(),
      }));
    }, 2000);

    // Random detection events
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
    }, 8000);

    return () => {
      clearTimeout(connectTimer);
      clearInterval(moveTimer);
      clearInterval(sensorTimer);
      clearInterval(detectionTimer);
    };
  }, []);

  const dismissDetection = useCallback(() => setActiveDetection(null), []);

  const sendControl = useCallback((x: number, y: number) => {
    if (USE_MOCK) return; // mock: robot moves on its own
    // Real: send via STOMP to /app/control
    console.log("control", { x, y });
  }, []);

  const sendCommand = useCallback(async (type: string) => {
    if (USE_MOCK) {
      const modeMap: Record<string, RobotStatus["mode"]> = {
        explore: "EXPLORE",
        return: "RETURN",
      };
      const newMode = modeMap[type] ?? "IDLE";
      mockMode.current = newMode;
      if (newMode === "RETURN") {
        waypointIdx.current = 0;
      }
      setStatus(s => ({ ...s, mode: newMode }));
      return;
    }
    await fetch(API.CMD(type), { method: "POST" });
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

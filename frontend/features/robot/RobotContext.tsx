"use client";

import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import {
  INITIAL_SENSORS,
  BATTERY_ABORT_PCT,
  type SensorReading,
  type DetectionEvent,
  type RobotStatus,
  type MissionState,
} from "./mockData";

import { api, ApiError, type CommandType } from "@/lib/api";
import {
  createStompClient,
  missionEventsTopic,
  missionEncountersTopic,
  type MissionEventMessage,
  type EncounterChangedMessage,
} from "@/lib/realtime";
import type { Client, StompSubscription } from "@stomp/stompjs";

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

interface RobotContextValue {
  // 지도는 여기서 나가지 않는다. 실시간 SLAM 지도는 젯슨의 foxglove_bridge 에
  // 직접 붙어 받고(LiveMap, S15P11A301-227), 저장된 지도는 조회 API 로 받는다
  // (MissionMap). 목업 격자를 들고 있을 이유가 없어졌다.
  // Status
  status: RobotStatus;
  // Sensors
  sensors: SensorReading;
  // Detections — 실 encounter 폴링이 채운다. 상단 배지의 출처다.
  detections: DetectionEvent[];
  // Control
  sendControl: (x: number, y: number) => void;
  sendCommand: (type: string) => Promise<void>;
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
  const [sensors, setSensors] = useState<SensorReading>(INITIAL_SENSORS);
  const [detections, setDetections] = useState<DetectionEvent[]>([]);
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
    errorCount: 0,
    warningCount: 2,
    infoCount: 7,
  });

  // 목 임무 상태. 순찰 경로 인덱스와 가동 시각은 목업 주행 애니메이션과 함께
  // 뺐다 (S15P11A301-227) — 읽는 화면이 없다.
  const mockMission = useRef<MissionState>("SAFE_IDLE");

  // 실 임무 상태. missionId 가 있으면 임무 상태는 서버(STOMP 푸시·백업 폴링)가
  // 결정하고, 목 시뮬레이션의 가짜 탐지·자동 전이는 멈춘다.
  const [missionId, setMissionId] = useState<string | null>(null);
  const missionIdRef = useRef<string | null>(null);

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
    // wsConnected 는 더 이상 목이 아니다 — 실제 STOMP 연결 상태(아래)가 결정한다.
    const connectTimer = setTimeout(() => {
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

    // 10Hz 목업 주행 애니메이션을 걷어냈다 (S15P11A301-227).
    //
    // 메인 지도가 실시간 SLAM 지도로 바뀌면서 이 타이머의 산출물(목업 격자,
    // 가짜 좌표, 순찰 궤적)을 읽는 화면이 하나도 남지 않았다. 같이 갱신하던
    // status.speed·heading·uptime 도 표시하는 곳이 없다 — 근거 없는 지표라
    // 이미 화면에서 뺐다(S15P11A301-200·223).
    //
    // 남겨 두면 초당 10번 14400칸 격자를 복사하면서 아무것도 그리지 않는다.

    // 1Hz 탐사 타이머를 걷어냈다 (S15P11A301-223).
    //
    // 잔여 탐사 시간 게이지가 사라졌고, 그 타이머가 하던 나머지 일도 남길
    // 이유가 없었다. 제한 시간에 도달하면 임무 상태를 스스로 RETURNING 으로
    // 바꾸는 로직이 들어 있었는데, 서버에 없는 프런트엔드 상수(7분)를 근거로
    // 화면이 상태를 만들어 내는 것이었다. 실제로 "탐사 중"인데 잔여 시간
    // 00:00 에 게이지가 꽉 찬 화면이 나왔다.
    //
    // 종료 조건 판단은 로봇이 한다(명세 23.4). 화면은 결과를 받는다.

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

    // 가짜 탐지 생성기는 뺐다 (S15P11A301-196). 배지의 탐지 수는 실 임무의
    // encounter 폴링(아래)만 채운다 — 목업 숫자가 섞이면 데모에서 실제 발견과
    // 구분할 수 없다. PERSON_APPROACHING 같은 중간 상태도 서버가 아직 노출하지
    // 않으므로(SERVER_MISSION_STATE 5종) 화면에 지어내지 않는다.

    return () => {
      clearTimeout(connectTimer);
      clearInterval(sensorTimer);
    };
  }, []);

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
    // 실제 상태는 STOMP 푸시(없으면 백업 폴링)가 서버 값으로 덮는다.
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
          }
          break;

        case "return":
          if (from !== "ESTOP" && from !== "ERROR") {
            missionState = "RETURNING";
            controlMode = "AUTO";
          }
          break;

        // manual/auto 는 controlMode 만 바꾼다. missionState 는 건드리지 않는다
        // (S15P11A301-200).
        //
        // 이전에는 manual 이 missionState 를 "MANUAL" 로 세웠는데, 서버는 MANUAL 을
        // 모르므로(SERVER_MISSION_STATE 5종) 3초 폴링이 즉시 EXPLORING 으로
        // 덮었다. 그러면 controlMode 만 MANUAL 로 남아 두 값이 어긋나고, auto 의
        // 조건(from === "MANUAL")이 영원히 거짓이 되어 **자율 버튼이 눌리지
        // 않았다.** 실기기에서 "탐사 중 + 수동"으로 굳은 상태가 그것이다.
        //
        // missionState 의 단일 출처는 서버다. 서버가 모르는 상태를 화면이
        // 지어내면 폴링이 바로 지운다 — 지어내지 않는 것이 맞다.
        //
        // 26.3의 "MANUAL 종료는 PAUSED로 복귀"는 실제 제어 세션이 붙을 때
        // 지켜야 한다(S15P11A301-39). 지금 이 토글은 로봇에 아무것도 보내지
        // 않으므로 되돌릴 주행 상태도 없고, 표시만 바꾸는 조작이 실제 임무를
        // 일시정지시키면 그쪽이 더 위험하다.
        case "manual":
          if (from !== "ESTOP" && from !== "ERROR") controlMode = "MANUAL";
          break;

        case "auto":
          controlMode = "AUTO";
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

  // ── 실시간 반영 ──────────────────────────────────────────────────────────
  // 주 채널은 STOMP 푸시(S15P11A301-204), REST 폴링은 끊김 대비 저빈도 백업이다.
  // 어느 쪽이 가져와도 서버 상태가 진실이라는 원칙(27.3)은 같다.

  /** 서버 상태를 화면에 반영한다. 푸시·폴링 공용. COMPLETED 는 임무를 닫는다. */
  const applyServerStatus = useCallback((serverStatus: string) => {
    const mapped = SERVER_MISSION_STATE[serverStatus];
    if (!mapped) return;
    pendingCommand.current = null;
    mockMission.current = mapped;
    setStatus(s => ({ ...s, missionState: mapped }));
    if (serverStatus === "COMPLETED") {
      // 다음 explore 가 새 임무를 만들 수 있게 활성 임무를 비운다.
      missionIdRef.current = null;
      setMissionId(null);
    }
  }, []);

  // 푸시와 폴링이 같은 발견을 두 번 목록에 넣지 않도록 본 것을 공유한다.
  const seenEncounters = useRef(new Set<string>());
  useEffect(() => { seenEncounters.current.clear(); }, [missionId]);

  const addDetection = useCallback(
    (id: string, at: number, mapX: number | null, mapY: number | null) => {
      if (seenEncounters.current.has(id)) return;
      seenEncounters.current.add(id);
      const event: DetectionEvent = {
        id,
        timestamp: at,
        confidence: 1,
        thumbnailColor: "hsl(20, 60%, 30%)",
        location:
          mapX !== null && mapY !== null
            ? `(${mapX.toFixed(1)}, ${mapY.toFixed(1)})`
            : "위치 미기록",
      };
      setDetections(d => [event, ...d].slice(0, 20));
    }, []);

  // STOMP 연결은 임무와 무관하게 유지한다 — 헤더의 연결 표시등이 이 상태다.
  // 구독은 임무 단위라 missionId 가 바뀌면 갈아타고, 재연결 시 onConnect 가 복구한다.
  const stompRef = useRef<Client | null>(null);
  const subsRef = useRef<StompSubscription[]>([]);

  const subscribeMission = useCallback((client: Client, mid: string | null) => {
    subsRef.current.forEach(s => { try { s.unsubscribe(); } catch { /* 이미 끊김 */ } });
    subsRef.current = [];
    if (!mid || !client.connected) return;
    subsRef.current = [
      client.subscribe(missionEventsTopic(mid), msg => {
        const ev = JSON.parse(msg.body) as MissionEventMessage;
        // 푸시는 ACK 반영 직후의 새 상태다 — 폴링과 달리 유예 없이 즉시 반영한다.
        if (ev.type === "MISSION_STATUS") applyServerStatus(ev.status);
      }),
      client.subscribe(missionEncountersTopic(mid), msg => {
        const ev = JSON.parse(msg.body) as EncounterChangedMessage;
        // 목록·배지는 신규 발견만 센다. phase 변화 상세는 블랙박스 화면이 다룬다.
        if (ev.phase === "CONFIRMED") {
          addDetection(ev.encounterId, Date.parse(ev.detectedAt), ev.mapX, ev.mapY);
        }
      }),
    ];
  }, [applyServerStatus, addDetection]);

  useEffect(() => {
    const client = createStompClient();
    stompRef.current = client;
    client.onConnect = () => {
      setWsConnected(true);
      subscribeMission(client, missionIdRef.current);
    };
    client.onWebSocketClose = () => setWsConnected(false);
    client.activate();
    return () => {
      subsRef.current = [];
      stompRef.current = null;
      void client.deactivate();
    };
  }, [subscribeMission]);

  useEffect(() => {
    if (stompRef.current) subscribeMission(stompRef.current, missionId);
  }, [missionId, subscribeMission]);

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
      } catch {
        // 서버에 못 붙으면 목 초기 상태로 남는다. 폴링이 아니므로 재시도하지 않는다.
      }
    })();
  }, []);

  // 임무 상태 백업 폴링 — STOMP 연결 중엔 15초로 늦추고, 끊기면 3초로 돌아온다.
  useEffect(() => {
    if (!missionId) return;
    const timer = setInterval(async () => {
      try {
        const m = await api.missionDetail(missionId);
        const mapped = SERVER_MISSION_STATE[m.status];
        if (!mapped) return;
        const pc = pendingCommand.current;
        if (pc && Date.now() - pc.at < COMMAND_GRACE_MS && mapped === pc.before) {
          // 아직 ACK 반영 전 — 낙관적 표시를 유지한다. (푸시에는 이 유예가 없다 —
          // 푸시 자체가 ACK 반영의 알림이라 옛 상태를 가져올 수 없다.)
          return;
        }
        applyServerStatus(m.status);
      } catch {
        // 일시적 네트워크 오류는 다음 폴링에 맡긴다.
      }
    }, wsConnected ? 15_000 : 3000);
    return () => clearInterval(timer);
  }, [missionId, wsConnected, applyServerStatus]);

  // encounter 백업 폴링 — 연결 중 30초, 끊기면 5초. 새 발견을 목록·배지에 반영한다.
  // 첫 실행이 기존 발견을 조용히 채우는 복구 역할도 그대로 한다(배지 강조는
  // 배지 쪽 이전 값 비교 담당, S15P11A301-196).
  useEffect(() => {
    if (!missionId) return;
    const timer = setInterval(async () => {
      try {
        const list = await api.missionEncounters(missionId);
        for (const e of list) {
          addDetection(e.id, Date.parse(e.startedAt), e.mapX, e.mapY);
        }
      } catch {
        // 다음 폴링에 맡긴다.
      }
    }, wsConnected ? 30_000 : 5000);
    return () => clearInterval(timer);
  }, [missionId, wsConnected, addDetection]);

  return (
    <RobotCtx.Provider value={{
      status, sensors,
      detections,
      sendControl, sendCommand,
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

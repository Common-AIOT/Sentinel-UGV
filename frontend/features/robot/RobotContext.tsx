"use client";

import React, { createContext, useContext, useEffect, useRef, useState, useCallback } from "react";
import {
  INITIAL_SENSORS,
  INITIAL_MOTION,
  type SensorReading,
  type MotionReading,
  type DetectionEvent,
  type RobotStatus,
  type MissionState,
} from "./mockData";
import { missionStateFromServer } from "./missionStatus";

import { api, ApiError, type CommandType } from "@/lib/api";
import { motionFromLatest } from "@/features/telemetry/motionReading";
import {
  createStompClient,
  missionEventsTopic,
  missionEncountersTopic,
  type MissionEventMessage,
  type EncounterChangedMessage,
} from "@/lib/realtime";
import type { Client, StompSubscription } from "@stomp/stompjs";

// 명령·조회는 실 API(@/lib/api)를 쓴다. 온습도·MCU 도 실측 텔레메트리다(#205).
// 남은 목은 접속 연출(1초 뒤 connected)과 lidar·camera 건강 표시 정도다.
export const USE_MOCK = true;

/** 확정 로봇 이름 (mqtt-setup·mvp-week-plan). */
const ROBOT_ID = "SENTINEL-01";

/**
 * 명령 거부 사유 → 관제 표현 (#207). 모르는 코드는 원문 그대로 보여준다.
 *
 * **젯슨이 실제로 낼 수 있는 코드를 전부 적는다** (S15P11A301-298). 종전에는 둘뿐이었고
 * 그중 `ROBOT_BUSY` 는 젯슨이 내지 않는 코드였다 — 즉 실제로 오는 거부는 거의 다
 * 원문 코드가 그대로 화면에 찍혔다. 운영자에게 `MANUAL_INPUT_ACTIVE` 는 아무 뜻이
 * 없고, 필요한 것은 **무엇을 하면 되는지**다.
 *
 * 출처는 `sentinel_mission/mission_state.py` 의 REASON_* 블록이다.
 */
const REASON_LABEL: Record<string, string> = {
  ROBOT_BUSY: "로봇이 다른 동작을 처리 중",
  NOT_IMPLEMENTED: "로봇이 지원하지 않는 명령",
  // 「자율」이 거부되는 유일한 정상 사유. 재시도가 답이다.
  MANUAL_INPUT_ACTIVE:
    "모바일 조종 입력이 계속 들어오는 중 — 조종을 멈춘 뒤 다시 시도하세요",
  MOTOR_BOARD_NO_ACK: "모터 보드가 응답하지 않음 — 로봇 전원·USB 연결을 확인하세요",
  MOTOR_BOARD_REJECTED: "모터 보드가 명령을 거부함",
  ESTOP_ACTIVE: "비상 정지 상태 — 물리 버튼을 확인하고 해제한 뒤 다시 시도하세요",
  ERROR_LATCHED: "결함 정지 상태 — 원인을 확인해야 합니다",
  INVALID_STATE: "지금 상태에서는 할 수 없는 명령",
  DUPLICATE_COMMAND: "이미 처리한 명령",
  MISSION_MANAGER_DOWN: "로봇의 임무 관리 노드가 떠 있지 않음",
  MALFORMED_COMMAND: "명령 형식 오류",
};
const COMMAND_LABEL: Record<string, string> = {
  START: "시작", PAUSE: "일시정지", RESUME: "재개", RETURN: "복귀", STOP: "정지",
  MANUAL: "수동 전환", AUTO: "자율 전환",
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
  // 주행 지표 (#300). 엔코더(ESP32) 기반이라 보드가 빠지면 비는 것이 정상이다.
  motion: MotionReading;
  // Detections — 실 encounter 폴링이 채운다. 상단 배지의 출처다.
  detections: DetectionEvent[];
  // Control
  sendControl: (x: number, y: number) => void;
  sendCommand: (type: string) => Promise<void>;
  // Mission
  missionId: string | null;
  // 명령 결과 알림 (S15P11A301-207) — 거부·실패·무응답을 화면이 설명한다.
  commandAlert: string | null;
  dismissCommandAlert: () => void;
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
  const [motion, setMotion] = useState<MotionReading>(INITIAL_MOTION);
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
  // 거부된 명령은 이 유예에 걸리지 않는다 — watchCommand 가 거부를 확인하는 즉시
  // applyServerStatus 로 되돌린다(S15P11A301-316).
  const pendingCommand = useRef<{ before: MissionState; at: number } | null>(null);
  const COMMAND_GRACE_MS = 10_000;

  /**
   * 서버 상태를 화면에 반영한다. 푸시·폴링·명령 거부 공용. COMPLETED 는 임무를 닫는다.
   *
   * `controlMode` 도 서버 값에서 파생한다 (S15P11A301-298). 이것이 있어야
   * **새로고침 후에도 수동 표시가 유지된다** — 종전에는 낙관적 전이만 있어
   * 페이지를 다시 열면 자율로 보였다. 파생 규칙은 젯슨
   * `mission_state.control_mode` 와 같다(MANUAL 이 아니면 AUTO).
   */
  const applyServerStatus = useCallback((serverStatus: string) => {
    const mapped = missionStateFromServer(serverStatus);
    if (!mapped) return;
    pendingCommand.current = null;
    mockMission.current = mapped;
    setStatus(s => ({
      ...s,
      missionState: mapped,
      controlMode: mapped === "MANUAL" ? "MANUAL" : "AUTO",
    }));
    if (serverStatus === "COMPLETED") {
      // 다음 explore 가 새 임무를 만들 수 있게 활성 임무를 비운다.
      missionIdRef.current = null;
      setMissionId(null);
    }
  }, []);

  // ── 명령 결과 감시 (S15P11A301-207) ──────────────────────────────────────
  // 202 는 "보냈다"일 뿐이라, 거부되면 화면이 조용히 원래 상태로 되돌아가기만
  // 했다. 발급 후 2·5·10초에 결과를 확인해 거부·실패는 사유와 함께, 10초
  // 무응답은 "로봇 응답 없음"으로 알린다 — 꺼진 로봇에 명령한 경우가 그 예다.
  const [commandAlert, setCommandAlert] = useState<string | null>(null);
  const dismissCommandAlert = useCallback(() => setCommandAlert(null), []);

  const watchCommand = useCallback((mid: string, commandId: string, type: string) => {
    const name = COMMAND_LABEL[type] ?? type;
    const checkAt = [2000, 5000, 10_000];
    const check = async (idx: number) => {
      try {
        const found = (await api.missionCommands(mid)).find(c => c.commandId === commandId);
        if (!found || found.result === "PENDING") {
          if (idx + 1 < checkAt.length) {
            setTimeout(() => void check(idx + 1), checkAt[idx + 1] - checkAt[idx]);
          } else {
            setCommandAlert(`${name} 명령에 로봇이 응답하지 않습니다 — 로봇 전원·연결을 확인하세요`);
          }
          return;
        }
        if (found.result === "ACCEPTED" || found.result === "EXECUTED") return; // 성공은 조용히
        const reason = found.reasonCode
          ? (REASON_LABEL[found.reasonCode] ?? found.reasonCode)
          : found.result === "FAILED" ? "전달 실패" : found.result;
        setCommandAlert(`${name} 명령이 거부되었습니다 — ${reason}`);
        // **낙관적 표시를 즉시 되돌린다** (S15P11A301-316). 거부는 서버 상태를
        // 바꾸지 않으므로 푸시가 오지 않고, 종전에는 최대 15초 뒤 백업 폴링이
        // 조용히 표시를 되돌렸다 — 조작자에게는 "가만히 있는데 갑자기 탐사 중으로
        // 바뀌는" 것으로 보였다. 사유 알림과 같은 순간에 표시도 되돌린다.
        applyServerStatus((await api.missionDetail(mid)).status);
      } catch {
        // 조회 실패는 알림을 만들지 않는다 — 상태 폴링·푸시가 진실을 따라잡는다.
      }
    };
    setTimeout(() => void check(0), checkAt[0]);
  }, [applyServerStatus]);

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
        // mcuConnected 는 이제 실측(#205 센서 폴링)만 쓴다 — 목으로 꾸미지 않는다.
        health: { ...s.health, lidarOk: true, cameraOk: true },
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

    // 2초 난수 센서 목업을 걷어냈다 (S15P11A301-205). 온습도는 이제 실측
    // 텔레메트리(아래 폴링)가 유일한 출처다 — 실값이 없으면 결측(—)으로 보이는
    // 것이 맞고, 그럴싸한 난수는 데모에서 실측과 구분이 안 된다.
    // 배터리 20% 자동 복귀 목업도 함께 뺐다 — 판단은 로봇이 한다(23.4).

    // 가짜 탐지 생성기는 뺐다 (S15P11A301-196). 배지의 탐지 수는 실 임무의
    // encounter 폴링(아래)만 채운다 — 목업 숫자가 섞이면 데모에서 실제 발견과
    // 구분할 수 없다. PERSON_APPROACHING 같은 중간 상태도 화면이 지어내지 않는다 —
    // 이제 로봇이 보고하고 서버가 담아 오며(S15P11A301-316), 그것이 유일한 출처다.

    return () => {
      clearTimeout(connectTimer);
    };
  }, []);

  // ── 센서 실측 폴링 (S15P11A301-205·255) ──────────────────────────────────
  // 임무 중에는 임무 텔레메트리의 마지막 버킷을, 대기 중에는 임무 무관 최신값
  // (/telemetry/latest)을 쓴다 — 차량이 임무 없이 켜져 있는 시간이 길어서다.
  // 어느 쪽이든 60초 넘게 오래된 값은 결측으로 보여준다 — 죽은 센서를 살아 있는
  // 것처럼 보여주지 않는다(젯슨 6초 null 규칙과 같은 원칙). 대기 값은 시각이
  // 그룹별(온습도·MCU)로 따로 오므로 각각 판정한다.
  useEffect(() => {
    let cancelled = false;
    const FRESH_MS = 60_000;
    const fresh = (iso: string | null) => iso !== null && Date.now() - Date.parse(iso) <= FRESH_MS;

    const poll = async () => {
      try {
        if (missionId) {
          const from = new Date(Date.now() - FRESH_MS).toISOString();
          const points = await api.missionTelemetry(missionId, 10, from);
          if (cancelled) return;
          if (points.length === 0) {
            setSensors(INITIAL_SENSORS);
            setMotion(INITIAL_MOTION);
            setStatus(s => ({ ...s, health: { ...s.health, mcuConnected: null } }));
            return;
          }
          const last = points[points.length - 1];
          setSensors({
            temperature: last.temperature,
            humidity: last.humidity,
            mcuConnected: last.mcuConnected,
            updatedAt: Date.parse(last.time),
          });
          // 주행 지표는 임무 중에도 같은 응답에 이미 들어 있다(#205).
          setMotion({
            linearVelocity: last.linearVelocity,
            angularVelocity: last.angularVelocity,
            updatedAt: Date.parse(last.time),
          });
          // MCU 연결은 상태판 건강 표시와도 같은 사실이어야 한다.
          setStatus(s => ({ ...s, health: { ...s.health, mcuConnected: last.mcuConnected } }));
          return;
        }

        const d = await api.telemetryLatest();
        if (cancelled) return;
        const envFresh = fresh(d.environmentTime);
        const mcuFresh = fresh(d.mcuTime);
        const mcu = mcuFresh ? d.mcuConnected : null;
        setSensors({
          temperature: envFresh ? d.temperature : null,
          humidity: envFresh ? d.humidity : null,
          mcuConnected: mcu,
          updatedAt: envFresh && d.environmentTime !== null ? Date.parse(d.environmentTime) : null,
        });
        // 신선도 판정과 결측 처리는 motionFromLatest 가 갖는다 — 시험이 지킨다(#300).
        setMotion(motionFromLatest(d, Date.now()));
        setStatus(s => ({ ...s, health: { ...s.health, mcuConnected: mcu } }));
      } catch {
        // 일시적 오류는 다음 폴링에 맡긴다. 마지막 표시값은 유지된다.
      }
    };
    void poll(); // 진입 즉시 한 번 — 첫 주기를 결측으로 비워두지 않는다.
    // 2초 (S15P11A301-300). 젯슨은 2Hz 로 보내므로 서버에는 0.5초마다 쌓인다 —
    // 5초는 값이 멈춰 보였다. 1초로 더 줄이면 CPU 숫자가 튀어 읽기 어렵고,
    // 0.5초 밑으로는 같은 값만 다시 받는다. 요청은 브라우저당 초당 0.5건이다.
    const timer = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [missionId]);

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
    if (
      type === "explore" || type === "return" || type === "pause" ||
      type === "manual" || type === "auto"
    ) {
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

      // **manual/auto 는 임무를 만들지 않는다** (S15P11A301-298). 위 생성 분기가
      // `type === "explore"` 로 게이트돼 있으므로, 진행 중 임무가 없으면 여기서
      // 명령이 나가지 않는다. 조용히 넘기면 조작자가 「수동」을 눌렀는데 아무 일도
      // 일어나지 않고 이유도 알 수 없으므로, 던져서 호출부가 설명하게 한다.
      if (!mid) {
        if (type === "manual" || type === "auto") {
          throw new Error("진행 중인 임무가 없습니다");
        }
      }

      if (mid) {
        const cmd: CommandType =
          type === "return" ? "STOP"
          : type === "pause" ? "PAUSE"
          : type === "manual" ? "MANUAL"
          : type === "auto" ? "AUTO"
          : resuming ? "RESUME"
          : "START";
        pendingCommand.current = { before: mockMission.current, at: Date.now() };
        const issued = await api.issueCommand(mid, cmd);
        watchCommand(mid, issued.commandId, cmd); // 결과 감시 — 거부·무응답이면 알림(#207)
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
          // 이 명령은 STOP 을 보내 **임무를 종료**한다. 복귀 주행이 아니다
          // (S15P11A301-274). 종전에는 낙관적으로 RETURNING("복귀 중")을 세웠는데,
          // RETURNING 은 미구현(S15P11A301-246)이라 로봇이 결코 그 상태가 되지
          // 않는다. 즉 화면만 복귀하는 것처럼 보였다. 대기 상태에서 잘못 누르면
          // 젯슨은 무의미한 전이를 하고 화면은 "복귀 중"을 띄웠다.
          //
          // 백엔드가 STOP ACK 에서 missions.status 를 COMPLETED 로 닫고 그 값을
          // STOMP 로 밀어 주므로(CommandAckWriter), 낙관적 표시도 COMPLETED 로
          // 맞춘다. 서버가 곧 같은 값으로 덮으므로 깜빡임이 없다.
          if (from !== "ESTOP" && from !== "ERROR") {
            missionState = "COMPLETED";
            controlMode = "AUTO";
          }
          break;

        // 이제 둘 다 **실제 명령을 보내고 missionState 도 함께 바꾼다**
        // (S15P11A301-298). 서버가 `missions.status = 'MANUAL'` 을 갖게 됐고
        // SERVER_MISSION_STATE 가 그것을 받으므로, 종전에 3초 폴링이 수동 표시를
        // 즉시 덮던 문제가 근원에서 사라졌다.
        //
        // 이 전이는 `pendingCommand` 를 거쳐 10초 grace 를 받는다 — 서버 왕복이
        // 끝나기 전의 폴링이 낙관적 표시를 되돌리지 않게 한다.
        case "manual":
          if (from !== "ESTOP" && from !== "ERROR") {
            missionState = "MANUAL";
            controlMode = "MANUAL";
          }
          break;

        // **PAUSED 이며 EXPLORING 이 아니다.** SR-008·26.3 이 자동 재개를 금지했다 —
        // 토글을 되돌렸다는 이유로 로봇이 갑자기 움직이면 그것이 사고다. 재개는
        // 상태 패널의 「탐사 재개」로 한다.
        //
        // 수동이 아니었으면 아무것도 바꾸지 않는다. 자율에서 「자율」을 다시 누른
        // 것이므로 임무를 일시정지시킬 이유가 없다.
        case "auto":
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
  }, [watchCommand]);

  // ── 실시간 반영 ──────────────────────────────────────────────────────────
  // 주 채널은 STOMP 푸시(S15P11A301-204), REST 폴링은 끊김 대비 저빈도 백업이다.
  // 어느 쪽이 가져와도 서버 상태가 진실이라는 원칙(27.3)은 같다.

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
        // 푸시·폴링과 **같은 경로**로 반영한다 (S15P11A301-316). 종전에는 여기서
        // missionState 만 세워 `controlMode` 가 null 로 남았고, 첫 백업 폴링까지
        // 최대 15초 동안 「수동 조종」인데 토글은 「자율」인 화면이 나왔다.
        applyServerStatus(active.status);
      } catch {
        // 서버에 못 붙으면 목 초기 상태로 남는다. 폴링이 아니므로 재시도하지 않는다.
      }
    })();
  }, [applyServerStatus]);

  // 임무 상태 백업 폴링 — STOMP 연결 중엔 15초로 늦추고, 끊기면 3초로 돌아온다.
  useEffect(() => {
    if (!missionId) return;
    const timer = setInterval(async () => {
      try {
        const m = await api.missionDetail(missionId);
        const mapped = missionStateFromServer(m.status);
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
      status, sensors, motion,
      detections,
      sendControl, sendCommand,
      missionId,
      commandAlert, dismissCommandAlert,
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

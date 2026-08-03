/**
 * 관제 실시간 채널 (STOMP over WebSocket, 명세 31-8 · S15P11A301-204).
 *
 * 백엔드가 MQTT 수신을 DB 에 쓴 직후 푸시하는 알림을 받는다. DB 가 진실이고
 * 이 채널은 알림이므로, 놓친 메시지는 REST 폴링(저빈도 백업)이 메운다.
 */
import { Client } from "@stomp/stompjs";
import { API_BASE } from "./api";

/** backend realtime/MissionEventMessage 와 1:1 */
export interface MissionEventMessage {
  type: string; // "MISSION_STATUS"
  missionId: string;
  status: string; // 서버 missions.status 5종
  at: string;
}

/** backend realtime/EncounterChangedMessage 와 1:1 */
export interface EncounterChangedMessage {
  encounterId: string;
  phase: string; // CONFIRMED | APPROACHED | ENDED | REDETECTED | LOST
  personCount: number | null;
  mapX: number | null;
  mapY: number | null;
  detectedAt: string;
}

/** https://api.… → wss://api.…/ws, http://localhost:8080 → ws://…/ws */
export const WS_URL = API_BASE.replace(/^http/, "ws") + "/ws";

export const missionEventsTopic = (missionId: string) =>
  `/topic/missions/${missionId}/events`;
export const missionEncountersTopic = (missionId: string) =>
  `/topic/missions/${missionId}/encounters`;

/** 끊기면 5초 간격 자동 재연결. 구독 복구는 onConnect 콜백 쪽 책임이다. */
export function createStompClient(): Client {
  return new Client({ brokerURL: WS_URL, reconnectDelay: 5000 });
}

/**
 * Foxglove SDK 프로토콜로 `/map`을 구독한다 (S15P11A301-227).
 *
 * `@foxglove/ws-protocol`을 쓰지 않는다. 그 라이브러리는 서브프로토콜
 * `foxglove.websocket.v1`을 말하는데 이 bridge(3.4.2 / foxglove-sdk-cpp 0.25.3)는
 * **`foxglove.sdk.v1`만 받는다.** 다른 이름으로 붙으면 HTTP 400 으로 거부한다 —
 * 실측으로 확인했다.
 *
 * 필요한 흐름이 짧아서 직접 구현한다.
 *
 * ```text
 * 서버 → serverInfo                    capabilities 등
 * 서버 → advertise                     채널 목록 (topic, id, encoding)
 * 우리 → subscribe                     원하는 채널만
 * 서버 → 바이너리 프레임                [op=1][subId u32][ts u64][CDR]
 * ```
 *
 * bridge 는 읽기 전용으로 떠 있다(S15P11A301-224 에서 capabilities 를
 * `[connectionGraph]`로 좁혔다). 그래서 이 클라이언트도 구독만 한다.
 */

import { decodeOccupancyGrid, type OccupancyGrid } from "./occupancyGrid";

/** bridge 가 요구하는 서브프로토콜. 틀리면 HTTP 400 이다. */
const SUBPROTOCOL = "foxglove.sdk.v1";

/** 바이너리 프레임의 op 코드. 1 = MessageData. */
const OP_MESSAGE_DATA = 1;

/** `[op:1][subscriptionId:4][timestamp:8]` 다음이 페이로드다. */
const MESSAGE_HEADER_BYTES = 13;

const MAP_TOPIC = "/map";

/** 우리가 정하는 구독 번호. 채널이 하나뿐이라 고정해도 된다. */
const SUBSCRIPTION_ID = 1;

export type MapClientState =
  | "connecting"
  | "waiting"
  | "streaming"
  | "disconnected";

export interface MapClientHandlers {
  onGrid: (grid: OccupancyGrid) => void;
  onState: (state: MapClientState, detail?: string) => void;
}

/** 재연결 대기(ms). 고정 간격이면 bridge 재시작 때 몰려 붙는다. */
const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 15_000;

/**
 * 지도 구독을 유지한다. `stop()`을 부를 때까지 재연결한다.
 *
 * 재연결을 두는 이유는 bridge 가 개발 중 내려갔다 오기 때문이다
 * (`viz_down.sh`/`viz_up.sh`). 그때 화면이 영구히 비면 원인을 화면에서 알 수 없다.
 */
export function startMapClient(
  url: string,
  handlers: MapClientHandlers,
): () => void {
  let socket: WebSocket | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let stopped = false;

  const connect = () => {
    if (stopped) return;
    handlers.onState("connecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(url, [SUBPROTOCOL]);
    } catch (error) {
      // 잘못된 URL 은 생성자에서 던진다. 재연결해도 같으므로 이유를 남긴다.
      scheduleRetry(error instanceof Error ? error.message : "주소가 올바르지 않습니다");
      return;
    }
    socket = ws;
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      attempt = 0;
      handlers.onState("waiting");
    };

    ws.onmessage = event => {
      if (typeof event.data === "string") {
        handleJson(ws, event.data);
        return;
      }
      handleBinary(event.data as ArrayBuffer);
    };

    ws.onerror = () => {
      // onerror 는 이유를 주지 않는다(브라우저 보안). onclose 가 뒤따르므로
      // 여기서 재연결을 걸지 않는다 — 걸면 두 번 붙는다.
    };

    ws.onclose = event => {
      socket = null;
      // 1006 은 서버가 없거나 TLS 핸드셰이크가 깨진 경우다. 시연 환경에서
      // 가장 흔한 실패이므로 그것만 따로 짚어 준다.
      scheduleRetry(
        event.code === 1006
          ? "젯슨에 닿지 못했습니다. 같은 네트워크인지 확인하세요."
          : `연결이 끊겼습니다 (코드 ${event.code})`,
      );
    };
  };

  const handleJson = (ws: WebSocket, raw: string) => {
    let message: unknown;
    try {
      message = JSON.parse(raw);
    } catch {
      return; // 모르는 프레임은 무시한다. 프로토콜이 늘어나도 깨지지 않는다.
    }
    if (typeof message !== "object" || message === null) return;
    const payload = message as { op?: string; channels?: unknown };
    if (payload.op !== "advertise" || !Array.isArray(payload.channels)) return;

    const channel = payload.channels.find(
      (c): c is { id: number; topic: string } =>
        typeof c === "object" &&
        c !== null &&
        (c as { topic?: unknown }).topic === MAP_TOPIC,
    );
    if (!channel) return;

    ws.send(
      JSON.stringify({
        op: "subscribe",
        subscriptions: [{ id: SUBSCRIPTION_ID, channelId: channel.id }],
      }),
    );
  };

  const handleBinary = (buffer: ArrayBuffer) => {
    if (buffer.byteLength < MESSAGE_HEADER_BYTES) return;
    const view = new DataView(buffer);
    if (view.getUint8(0) !== OP_MESSAGE_DATA) return;
    if (view.getUint32(1, true) !== SUBSCRIPTION_ID) return;

    try {
      handlers.onGrid(decodeOccupancyGrid(buffer.slice(MESSAGE_HEADER_BYTES)));
      handlers.onState("streaming");
    } catch (error) {
      // 디코딩 실패를 삼키지 않는다. 삼키면 "지도가 안 나온다"만 남고 이유가
      // 사라진다 — 이 프로젝트에서 반복해서 겪은 형태다.
      handlers.onState(
        "waiting",
        error instanceof Error ? error.message : "지도를 해석하지 못했습니다",
      );
    }
  };

  const scheduleRetry = (detail: string) => {
    if (stopped) return;
    handlers.onState("disconnected", detail);
    const delay = Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_MAX_MS);
    attempt += 1;
    retryTimer = setTimeout(connect, delay);
  };

  connect();

  return () => {
    stopped = true;
    if (retryTimer !== null) clearTimeout(retryTimer);
    // onclose 가 재연결을 걸지 않도록 핸들러를 먼저 뗀다.
    if (socket) {
      socket.onclose = null;
      socket.onerror = null;
      socket.close();
      socket = null;
    }
  };
}

/**
 * Foxglove SDK 프로토콜 클라이언트 시험 (S15P11A301-227).
 *
 * 브라우저 `WebSocket`을 가짜로 바꿔 프로토콜 흐름만 본다. 이 부분이 가장
 * 위험하다 — 서브프로토콜을 틀리면 bridge 가 HTTP 400 으로 거부하고, 구독을 안
 * 보내면 연결은 되는데 **메시지가 영원히 오지 않는다.** 둘 다 화면에서는
 * "지도가 안 나온다"로만 보인다.
 *
 * 실측으로 확인한 사실을 여기에 못박는다.
 *
 * ```text
 * 서브프로토콜   foxglove.sdk.v1        foxglove.websocket.v1 은 HTTP 400
 * 바이너리 형식  [op=1][subId u32][ts u64][CDR]
 * ```
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { startMapClient, type MapClientState } from "@/lib/foxgloveMapClient";

/** 캡처한 `/pose` CDR 전체. tests/robotPose.test.ts 와 같은 바이트다. */
const POSE_HEX =
  "000100003f4d706ae0c7d536040000006d617000671fbf58f28bc5bfc971fc62" +
  "c92f96bf000000000000000000000000000000000000000000000000d0248f31" +
  "457175bf42947b43e3ffef3fa651a2fda7954d3fc309bca68eab2e3f00000000" +
  "00000000000000000000000000000000000000000000000000000000c309bca6" +
  "8eab2e3f59929d428afc4f3f0000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "000000001c6feb1524df1f3f";

/** 캡처한 `/map` CDR 헤더. tests/occupancyGrid.test.ts 와 같은 바이트다. */
const HEADER_HEX =
  "00010000e447706af84a0521040000006d6170000000000000000000cdcc4c3d" +
  "e0000000f2000000000000009d0d5023e6851ac0230ee4d5e1de16c000000000" +
  "0000000000000000000000000000000000000000000000000000000000000000" +
  "0000f03fc0d30000";
const CELLS = 224 * 242;

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

/** bridge 가 보내는 형태의 바이너리 프레임을 만든다. */
function messageFrame(subscriptionId: number, cdr?: Uint8Array): ArrayBuffer {
  if (!cdr) {
    const header = hexToBytes(HEADER_HEX);
    cdr = new Uint8Array(header.length + CELLS);
    cdr.set(header, 0);
    cdr.fill(0xff, header.length); // -1 = 미탐색
  }

  const frame = new Uint8Array(13 + cdr.length);
  const view = new DataView(frame.buffer);
  view.setUint8(0, 1); // op = MessageData
  view.setUint32(1, subscriptionId, true);
  // timestamp(5..12)는 0 그대로 둔다. 클라이언트가 읽지 않고, BigInt 리터럴은
  // tsconfig target 보다 높아 tsc 가 거부한다.
  frame.set(cdr, 13);
  return frame.buffer;
}

interface FakeSocket {
  url: string;
  protocols: string | string[] | undefined;
  sent: string[];
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: (() => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  close: () => void;
  closed: boolean;
}

let sockets: FakeSocket[] = [];

beforeEach(() => {
  sockets = [];
  vi.useFakeTimers();
  class Fake {
    url: string;
    protocols: string | string[] | undefined;
    sent: string[] = [];
    binaryType = "blob";
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    closed = false;
    constructor(url: string, protocols?: string | string[]) {
      this.url = url;
      this.protocols = protocols;
      sockets.push(this as unknown as FakeSocket);
    }
    send(data: string) {
      this.sent.push(data);
    }
    close() {
      this.closed = true;
    }
  }
  vi.stubGlobal("WebSocket", Fake);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function collect() {
  const grids: { width: number; height: number }[] = [];
  const poses: { x: number; y: number }[] = [];
  const states: MapClientState[] = [];
  const details: (string | undefined)[] = [];
  const stop = startMapClient("wss://jetson.example:8765", {
    onGrid: g => grids.push({ width: g.width, height: g.height }),
    onPose: p => poses.push({ x: p.x, y: p.y }),
    onState: (s, d) => {
      states.push(s);
      details.push(d);
    },
  });
  return { grids, poses, states, details, stop };
}

describe("startMapClient — 연결", () => {
  it("foxglove.sdk.v1 서브프로토콜로 붙는다", () => {
    // websocket.v1 로 붙으면 bridge 가 HTTP 400 으로 거부한다. 실측으로 확인했다.
    const { stop } = collect();
    expect(sockets[0].protocols).toEqual(["foxglove.sdk.v1"]);
    stop();
  });

  it("arraybuffer 로 받는다", () => {
    // blob 이면 동기 디코딩이 안 된다.
    const { stop } = collect();
    expect(sockets[0].binaryType).toBe("arraybuffer");
    stop();
  });
});

describe("startMapClient — 구독", () => {
  it("advertise 에서 /map 과 /pose 를 찾아 subscribe 를 보낸다", () => {
    const { stop } = collect();
    const ws = sockets[0];
    ws.onopen?.();
    ws.onmessage?.({
      data: JSON.stringify({
        op: "advertise",
        channels: [
          { id: 3, topic: "/scan", encoding: "cdr" },
          { id: 6, topic: "/map", encoding: "cdr" },
          { id: 5, topic: "/pose", encoding: "cdr" },
        ],
      }),
    });
    expect(ws.sent).toHaveLength(1);
    // 우리 구독 번호는 고정이고 channelId 만 서버 것을 따른다. 번호가 흔들리면
    // 수신 쪽 분기가 토픽을 잘못 짚는다.
    expect(JSON.parse(ws.sent[0])).toEqual({
      op: "subscribe",
      subscriptions: [
        { id: 1, channelId: 6 },
        { id: 2, channelId: 5 },
      ],
    });
    stop();
  });

  it("/map 만 있으면 /map 만 구독한다", () => {
    // SLAM 이 뜨기 전이나 화이트리스트가 좁을 때다. 없는 토픽을 구독하면
    // bridge 가 프레임을 안 보내고, 그것을 "지도 대기" 로 오해한다.
    const { stop } = collect();
    const ws = sockets[0];
    ws.onmessage?.({
      data: JSON.stringify({
        op: "advertise",
        channels: [{ id: 6, topic: "/map", encoding: "cdr" }],
      }),
    });
    expect(JSON.parse(ws.sent[0]).subscriptions).toEqual([
      { id: 1, channelId: 6 },
    ]);
    stop();
  });

  it("원하는 토픽이 하나도 없으면 구독하지 않는다", () => {
    // 아무 채널이나 구독하면 엉뚱한 메시지를 지도로 해석한다.
    const { stop } = collect();
    const ws = sockets[0];
    ws.onmessage?.({
      data: JSON.stringify({
        op: "advertise",
        channels: [{ id: 3, topic: "/scan", encoding: "cdr" }],
      }),
    });
    expect(ws.sent).toHaveLength(0);
    stop();
  });

  it("모르는 JSON 프레임에 깨지지 않는다", () => {
    const { stop } = collect();
    const ws = sockets[0];
    expect(() => {
      ws.onmessage?.({ data: "not json" });
      ws.onmessage?.({ data: JSON.stringify({ op: "serverInfo" }) });
      ws.onmessage?.({ data: JSON.stringify({ op: "advertise" }) });
    }).not.toThrow();
    expect(ws.sent).toHaveLength(0);
    stop();
  });
});

describe("startMapClient — 메시지", () => {
  it("바이너리 프레임을 격자로 푼다", () => {
    const { grids, states, stop } = collect();
    const ws = sockets[0];
    ws.onmessage?.({ data: messageFrame(1) });
    expect(grids).toEqual([{ width: 224, height: 242 }]);
    expect(states.at(-1)).toBe("streaming");
    stop();
  });

  it("/pose 프레임을 위치로 푼다", () => {
    const { poses, stop } = collect();
    sockets[0].onmessage?.({ data: messageFrame(2, hexToBytes(POSE_HEX)) });
    expect(poses).toHaveLength(1);
    expect(poses[0].x).toBeCloseTo(-0.168333, 5);
    stop();
  });

  it("구독 번호로 토픽을 가른다", () => {
    // 번호를 안 보고 아무 프레임이나 격자로 넘기면 /pose 364바이트가 격자
    // 디코더로 들어가 "지도를 해석하지 못했습니다" 만 남는다.
    const { grids, poses, stop } = collect();
    const ws = sockets[0];
    ws.onmessage?.({ data: messageFrame(2, hexToBytes(POSE_HEX)) });
    expect(grids).toHaveLength(0);
    ws.onmessage?.({ data: messageFrame(1) });
    expect(poses).toHaveLength(1);
    expect(grids).toHaveLength(1);
    stop();
  });

  it("/pose 만 와도 streaming 이 되지 않는다", () => {
    // 지도가 없는데 "실시간" 이라고 적으면 SLAM 이 안 도는 것을 화면에서 못
    // 잡는다. 상태는 지도 기준이다.
    const { states, stop } = collect();
    const ws = sockets[0];
    ws.onopen?.();
    ws.onmessage?.({ data: messageFrame(2, hexToBytes(POSE_HEX)) });
    expect(states.at(-1)).toBe("waiting");
    stop();
  });

  it("다른 구독 번호의 프레임은 무시한다", () => {
    const { grids, poses, stop } = collect();
    sockets[0].onmessage?.({ data: messageFrame(99) });
    expect(grids).toHaveLength(0);
    expect(poses).toHaveLength(0);
    stop();
  });

  it("op 가 MessageData 가 아니면 무시한다", () => {
    const { grids, stop } = collect();
    const frame = messageFrame(1);
    new DataView(frame).setUint8(0, 2); // 2 = 다른 op
    sockets[0].onmessage?.({ data: frame });
    expect(grids).toHaveLength(0);
    stop();
  });

  it("디코딩 실패를 삼키지 않고 이유를 알린다", () => {
    // 삼키면 "지도가 안 나온다"만 남고 원인이 사라진다.
    const { grids, states, details, stop } = collect();
    const broken = new Uint8Array(messageFrame(1)).slice(0, 60).buffer;
    sockets[0].onmessage?.({ data: broken });
    expect(grids).toHaveLength(0);
    expect(states.at(-1)).toBe("waiting");
    expect(details.at(-1)).toBeTruthy();
    stop();
  });
});

describe("startMapClient — 재연결", () => {
  it("끊기면 다시 붙는다", () => {
    const { states, stop } = collect();
    sockets[0].onclose?.({ code: 1006 });
    expect(states.at(-1)).toBe("disconnected");
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(2);
    stop();
  });

  it("1006 은 네트워크 문제로 안내한다", () => {
    // 시연에서 가장 흔한 실패다 — 외부망 노트북이면 젯슨에 닿지 못한다.
    const { details, stop } = collect();
    sockets[0].onclose?.({ code: 1006 });
    expect(details.at(-1)).toMatch(/네트워크/);
    stop();
  });

  it("재연결 간격이 늘어난다", () => {
    // 고정 간격이면 bridge 재시작 때 몰려 붙는다.
    const { stop } = collect();
    sockets[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(2);

    sockets[1].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(2); // 아직 — 두 번째는 2초를 기다린다
    vi.advanceTimersByTime(1_000);
    expect(sockets).toHaveLength(3);
    stop();
  });

  it("stop 뒤에는 다시 붙지 않는다", () => {
    const { stop } = collect();
    stop();
    sockets[0].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);
  });
});

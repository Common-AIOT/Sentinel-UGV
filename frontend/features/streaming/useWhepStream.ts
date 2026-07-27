"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * MediaMTX WHEP 클라이언트 (S15P11A301-107).
 *
 * S15P11A301-106이 Jetson에서 제공하는 WHEP 엔드포인트에 연결해 H.264 스트림을
 * 받는다. 규범은 명세 32-4다.
 *
 * 영상 상태는 UX가 아니라 안전 기능이다. 영상이 멈춘 상태에서 수동 전진을
 * 허용하면 조종자가 보이지 않는 상황에서 로봇을 움직이게 된다(SR-010).
 * 그래서 정지 판정을 프레임 수신 기준으로 하고 상태를 밖으로 노출한다.
 */

export type StreamState =
  | "CONNECTING"
  | "LIVE"
  | "DEGRADED"
  | "RECONNECTING"
  | "OFFLINE";

export type StreamPath = "LOCAL" | "REMOTE";

/** 명세 32-4의 임계값. 1초는 노란 경고, 3초는 빨간 경고와 전진 차단이다. */
const STALL_WARN_MS = 1000;
const STALL_BLOCK_MS = 3000;

/** DEGRADED 판정에 쓰는 최근 구간. 32-4가 "최근 5초 평균"으로 정한다. */
const DEGRADED_WINDOW_MS = 5000;

const RECONNECT_BACKOFF_MS = [1000, 2000, 4000, 8000];

/**
 * 연결 정체 감시 한도.
 *
 * 재시도 타이머가 어떤 이유로든 소실되면(effect 재실행과 cleanup이
 * 엇갈리는 경우 등) 어떤 이벤트도 오지 않아 영구히 CONNECTING에 갇힌다.
 * 실제로 그 상태를 관측했다. 시도 후 이 시간까지 연결되지 않으면
 * 원인에 의존하지 않고 강제로 다시 시도한다.
 */
const STALL_CONNECT_TIMEOUT_MS = 10000;
const STATS_INTERVAL_MS = 500;

export interface LatencySample {
  at: number;
  /** 추정 지연(ms). jitter buffer 지연과 RTT 절반을 합친 값이다. */
  estimateMs: number;
}

export interface WhepStatus {
  state: StreamState;
  /** 마지막 프레임 수신 후 경과(ms). 프레임을 못 받았으면 null. */
  staleMs: number | null;
  /** 1초 이상 정지. 노란 경고 대상. */
  stalledWarn: boolean;
  /** 3초 이상 정지. 빨간 경고와 신규 전진 차단 대상(SR-010). */
  stalledBlock: boolean;
  /**
   * 수신 지연(ms). 네트워크 왕복 절반 + jitter buffer + 조립 + 디코딩까지
   * 브라우저가 실제로 집계한 값의 합이다. 스트리밍에서 흔히 보는 지연
   * 지표에 가장 가깝다. Jetson 쪽 카메라 노출과 인코딩은 브라우저가 알 수
   * 없으므로 빠져 있다.
   */
  receiveLatencyMs: number | null;
  /** 버퍼 지연만(ms). 세부 확인용. */
  latencyMs: number | null;
  /** 프레임당 처리 지연(ms). 첫 패킷 수신부터 디코딩 완료까지. */
  processingMs: number | null;
  framesDecoded: number;
  path: StreamPath;
  error: string | null;

  /** 실제 디코딩 FPS. 서버 발행률이 아니라 브라우저가 그리는 값이다. */
  fps: number | null;
  width: number | null;
  height: number | null;
  /** 수신 비트레이트(kbps). bytesReceived 증분으로 계산한다. */
  bitrateKbps: number | null;
  /** 누적 패킷 손실률(%). 재전송으로 복구된 것은 제외되지 않는다. */
  lossPct: number | null;
  packetsLost: number | null;
  /** 브라우저가 집계한 정지 횟수와 총 정지 시간. 32-4 판정과 별개 지표다. */
  freezeCount: number | null;
  freezeSeconds: number | null;
  rttMs: number | null;
  jitterMs: number | null;
}

function endpointFor(path: StreamPath): string {
  const local = process.env.NEXT_PUBLIC_LOCAL_STREAM_URL;
  const remote = process.env.NEXT_PUBLIC_REMOTE_STREAM_URL;
  const chosen = path === "LOCAL" ? local : remote;
  // 주소를 코드에 박지 않는다(부록 D). 미설정이면 빈 문자열을 돌려주고
  // 호출부가 OFFLINE으로 처리하게 한다.
  return chosen ?? "";
}

/** 해당 경로의 엔드포인트가 설정돼 있는지. 미설정 경로는 UI에서 감춘다. */
export function isPathConfigured(path: StreamPath): boolean {
  return endpointFor(path) !== "";
}

/**
 * WHEP 핸드셰이크. SDP offer를 POST하고 answer를 받는다.
 * 반환값은 세션 삭제에 쓰는 Location URL이다.
 */
async function whepHandshake(
  endpoint: string,
  offer: RTCSessionDescriptionInit,
  signal: AbortSignal,
): Promise<{ answer: string; resourceUrl: string | null }> {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: offer.sdp,
    signal,
  });

  if (!response.ok) {
    throw new Error(`WHEP ${response.status} ${response.statusText}`);
  }

  const answer = await response.text();
  const location = response.headers.get("location");
  let resourceUrl: string | null = null;
  if (location) {
    try {
      resourceUrl = new URL(location, endpoint).toString();
    } catch {
      resourceUrl = null;
    }
  }
  return { answer, resourceUrl };
}

export function useWhepStream(enabled: boolean, path: StreamPath = "LOCAL") {
  const videoElRef = useRef<HTMLVideoElement | null>(null);
  // 받은 MediaStream을 따로 보관한다. ontrack은 한 번만 발화하므로
  // video 요소가 다시 마운트되면(메인 <-> 사이드바 전환) 새 요소에는
  // srcObject가 비어 영상이 나오지 않는다.
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const resourceRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 마지막 연결 시도 시각. 감시 타이머가 정체를 판정하는 기준이다.
  const lastAttemptAtRef = useRef<number>(0);

  const lastFrameAtRef = useRef<number | null>(null);
  const lastFramesDecodedRef = useRef(0);
  const latencyHistoryRef = useRef<LatencySample[]>([]);
  // 비트레이트는 누적 bytesReceived의 증분으로 계산한다.
  const lastBytesRef = useRef<{ at: number; bytes: number } | null>(null);

  const [status, setStatus] = useState<WhepStatus>({
    state: "OFFLINE",
    staleMs: null,
    stalledWarn: false,
    stalledBlock: false,
    receiveLatencyMs: null,
    latencyMs: null,
    processingMs: null,
    framesDecoded: 0,
    path,
    error: null,
    fps: null,
    width: null,
    height: null,
    bitrateKbps: null,
    lossPct: null,
    packetsLost: null,
    freezeCount: null,
    freezeSeconds: null,
    rttMs: null,
    jitterMs: null,
  });

  const patch = useCallback((next: Partial<WhepStatus>) => {
    setStatus((prev) => ({ ...prev, ...next }));
  }, []);

  /**
   * video 요소를 붙이는 callback ref.
   *
   * RefObject 대신 callback ref를 쓰는 이유는 요소가 다시 마운트될 때
   * 알림을 받아야 하기 때문이다. 메인과 사이드바를 전환하면 React가
   * VideoPanel을 언마운트하고 다른 위치에 새로 마운트하는데, 그때 새
   * 요소에 보관된 MediaStream을 다시 붙여야 한다.
   */
  const attachVideo = useCallback((element: HTMLVideoElement | null) => {
    videoElRef.current = element;
    if (element && mediaStreamRef.current) {
      if (element.srcObject !== mediaStreamRef.current) {
        element.srcObject = mediaStreamRef.current;
      }
      void element.play().catch(() => {});
    }
  }, []);

  const teardown = useCallback(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    abortRef.current?.abort();
    abortRef.current = null;

    // WHEP 세션을 서버에 명시적으로 반납한다. 하지 않으면 MediaMTX가
    // 타임아웃까지 세션을 붙잡는다.
    const resource = resourceRef.current;
    if (resource) {
      void fetch(resource, { method: "DELETE" }).catch(() => {});
      resourceRef.current = null;
    }

    pcRef.current?.close();
    pcRef.current = null;

    mediaStreamRef.current = null;
    if (videoElRef.current) {
      videoElRef.current.srcObject = null;
    }
    lastFrameAtRef.current = null;
    lastFramesDecodedRef.current = 0;
  }, []);

  const scheduleReconnect = useCallback(
    (reason: string) => {
      if (retryTimerRef.current) return;
      const delay =
        RECONNECT_BACKOFF_MS[
          Math.min(retryRef.current, RECONNECT_BACKOFF_MS.length - 1)
        ];
      retryRef.current += 1;
      // 백오프를 모두 소진했으면 OFFLINE으로 표시한다. 재시도는 최대
      // 간격으로 계속하므로 서버가 살아나면 자동 복구된다. 계속
      // RECONNECTING만 보여주면 조종자가 곧 복구된다고 오해한다.
      const exhausted = retryRef.current > RECONNECT_BACKOFF_MS.length;
      patch({ state: exhausted ? "OFFLINE" : "RECONNECTING", error: reason });
      retryTimerRef.current = setTimeout(() => {
        retryTimerRef.current = null;
        setConnectTick((tick) => tick + 1);
      }, delay);
    },
    [patch],
  );

  // 연결 트리거. 재연결 시 이 값을 올려 effect를 다시 돌린다.
  const [connectTick, setConnectTick] = useState(0);

  useEffect(() => {
    if (!enabled) {
      teardown();
      patch({ state: "OFFLINE", staleMs: null, stalledWarn: false, stalledBlock: false });
      return;
    }

    const endpoint = endpointFor(path);
    if (!endpoint) {
      patch({
        state: "OFFLINE",
        error:
          path === "LOCAL"
            ? "NEXT_PUBLIC_LOCAL_STREAM_URL이 설정되지 않았다"
            : "NEXT_PUBLIC_REMOTE_STREAM_URL이 설정되지 않았다",
      });
      return;
    }

    let cancelled = false;
    const abort = new AbortController();
    abortRef.current = abort;
    lastAttemptAtRef.current = Date.now();

    // 첫 시도만 CONNECTING이다. 재시도에서 CONNECTING으로 되돌리면
    // 화면이 계속 "연결 중"으로 보여 재연결 중인지 알 수 없다.
    if (retryRef.current === 0) {
      patch({ state: "CONNECTING", path, error: null });
    } else {
      patch({
        state: retryRef.current > RECONNECT_BACKOFF_MS.length
          ? "OFFLINE"
          : "RECONNECTING",
        path,
      });
    }

    const pc = new RTCPeerConnection({
      // LAN 시연에서는 호스트 candidate를 우선하고 STUN/TURN 의존을
      // 제거한다(32-4). 원격 관제는 선택 기능이며 별도 구성한다.
      iceServers: [],
    });
    pcRef.current = pc;
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    pc.ontrack = (event) => {
      if (cancelled) return;
      const stream = event.streams[0];
      if (!stream) return;

      // 버퍼 목표 지연을 최소로 낮춘다. 기본값은 매끄러운 재생을 위해
      // 여유를 두는데, 관제 영상은 조종 판단에 쓰이므로 지연이 더 중요하다.
      // 표준 API이며 Chrome이 지원한다. 미지원 브라우저에서는 무시된다.
      // 대가는 네트워크가 흔들릴 때 프레임 끊김이 늘어나는 것이다.
      const receiver = event.receiver as RTCRtpReceiver & {
        playoutDelayHint?: number;
      };
      if (receiver && "playoutDelayHint" in receiver) {
        try {
          receiver.playoutDelayHint = 0;
        } catch {
          // 지원하지 않는 브라우저는 그냥 기본값을 쓴다.
        }
      }
      mediaStreamRef.current = stream;
      const el = videoElRef.current;
      if (el && el.srcObject !== stream) {
        el.srcObject = stream;
        void el.play().catch(() => {});
      }
    };

    pc.onconnectionstatechange = () => {
      if (cancelled) return;
      if (pc.connectionState === "connected") {
        retryRef.current = 0;
        patch({ state: "LIVE", error: null });
      } else if (
        pc.connectionState === "failed" ||
        pc.connectionState === "disconnected"
      ) {
        scheduleReconnect(`peer ${pc.connectionState}`);
      }
    };

    (async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // ICE gathering 완료를 기다린다. WHEP은 단일 왕복이므로
        // trickle ICE를 쓰지 않는다.
        await new Promise<void>((resolve) => {
          if (pc.iceGatheringState === "complete") return resolve();
          const check = () => {
            if (pc.iceGatheringState === "complete") {
              pc.removeEventListener("icegatheringstatechange", check);
              resolve();
            }
          };
          pc.addEventListener("icegatheringstatechange", check);
          // 게더링이 끝나지 않아도 2초 후 진행한다. 호스트 candidate만
          // 필요한 LAN 환경에서는 충분하다.
          setTimeout(resolve, 2000);
        });

        const local = pc.localDescription;
        if (!local?.sdp) throw new Error("로컬 SDP 생성 실패");

        const { answer, resourceUrl } = await whepHandshake(
          endpoint,
          local,
          abort.signal,
        );
        if (cancelled) return;
        resourceRef.current = resourceUrl;
        await pc.setRemoteDescription({ type: "answer", sdp: answer });
      } catch (error) {
        if (cancelled) return;
        const message =
          error instanceof Error ? error.message : "알 수 없는 오류";
        scheduleReconnect(message);
      }
    })();

    return () => {
      cancelled = true;
      teardown();
    };
  }, [enabled, path, connectTick, patch, scheduleReconnect, teardown]);

  // 프레임 수신 감시와 지연 추정.
  useEffect(() => {
    if (!enabled) return;

    const id = setInterval(async () => {
      const pc = pcRef.current;
      const now = Date.now();

      if (!pc || pc.connectionState !== "connected") {
        // 정체 감시. 시도 후 한도를 넘겨도 연결되지 않고 예약된 재시도도
        // 없으면 강제로 다시 시도한다. 이 경로가 없으면 재시도 타이머가
        // 소실됐을 때 영구히 갇힌다(2026-07-27 관측).
        const stalled =
          lastAttemptAtRef.current > 0 &&
          now - lastAttemptAtRef.current > STALL_CONNECT_TIMEOUT_MS;
        if (stalled && retryTimerRef.current === null) {
          lastAttemptAtRef.current = now;
          retryRef.current += 1;
          setStatus((prev) => ({
            ...prev,
            state:
              retryRef.current > RECONNECT_BACKOFF_MS.length
                ? "OFFLINE"
                : "RECONNECTING",
            error: "연결 정체로 재시도",
          }));
          setConnectTick((tick) => tick + 1);
          return;
        }

        setStatus((prev) => {
          if (
            prev.state === "CONNECTING" ||
            prev.state === "RECONNECTING" ||
            prev.state === "OFFLINE"
          ) {
            return prev;
          }
          return { ...prev, staleMs: null, stalledWarn: false, stalledBlock: false };
        });
        return;
      }

      let framesDecoded = lastFramesDecodedRef.current;
      let jitterDelayMs: number | null = null;
      let rttMs: number | null = null;
      let fps: number | null = null;
      let width: number | null = null;
      let height: number | null = null;
      let bitrateKbps: number | null = null;
      let lossPct: number | null = null;
      let packetsLost: number | null = null;
      let freezeCount: number | null = null;
      let freezeSeconds: number | null = null;
      let jitterMs: number | null = null;
      let processingMs: number | null = null;

      try {
        const stats = await pc.getStats();
        stats.forEach((report) => {
          if (report.type === "inbound-rtp" && report.kind === "video") {
            if (typeof report.framesDecoded === "number") {
              framesDecoded = report.framesDecoded;
            }
            if (
              typeof report.jitterBufferDelay === "number" &&
              typeof report.jitterBufferEmittedCount === "number" &&
              report.jitterBufferEmittedCount > 0
            ) {
              jitterDelayMs =
                (report.jitterBufferDelay / report.jitterBufferEmittedCount) *
                1000;
            }
            if (typeof report.framesPerSecond === "number") {
              fps = report.framesPerSecond;
            }
            if (typeof report.frameWidth === "number") width = report.frameWidth;
            if (typeof report.frameHeight === "number") height = report.frameHeight;
            if (typeof report.jitter === "number") jitterMs = report.jitter * 1000;
            // totalProcessingDelay는 첫 패킷 수신부터 디코딩 완료까지의
            // 누적 시간이다. 프레임 수로 나누면 프레임당 처리 지연이 된다.
            // jitter buffer 대기가 이미 포함돼 있으므로 따로 더하지 않는다.
            if (
              typeof report.totalProcessingDelay === "number" &&
              typeof report.framesDecoded === "number" &&
              report.framesDecoded > 0
            ) {
              processingMs =
                (report.totalProcessingDelay / report.framesDecoded) * 1000;
            }
            if (typeof report.freezeCount === "number") {
              freezeCount = report.freezeCount;
            }
            if (typeof report.totalFreezesDuration === "number") {
              freezeSeconds = report.totalFreezesDuration;
            }
            if (typeof report.packetsLost === "number") {
              packetsLost = report.packetsLost;
              if (typeof report.packetsReceived === "number") {
                const total = report.packetsReceived + report.packetsLost;
                if (total > 0) lossPct = (report.packetsLost / total) * 100;
              }
            }
            // 비트레이트는 누적값의 증분으로만 구할 수 있다.
            if (typeof report.bytesReceived === "number") {
              const prev = lastBytesRef.current;
              if (prev && now > prev.at) {
                const deltaBits = (report.bytesReceived - prev.bytes) * 8;
                const deltaSec = (now - prev.at) / 1000;
                if (deltaBits >= 0 && deltaSec > 0) {
                  bitrateKbps = deltaBits / deltaSec / 1000;
                }
              }
              lastBytesRef.current = { at: now, bytes: report.bytesReceived };
            }
          }
          if (
            report.type === "candidate-pair" &&
            report.state === "succeeded" &&
            typeof report.currentRoundTripTime === "number"
          ) {
            rttMs = report.currentRoundTripTime * 1000;
          }
        });
      } catch {
        // getStats 실패는 치명적이지 않다. 다음 주기에 다시 시도한다.
      }

      // 프레임이 늘었으면 수신 시각을 갱신한다. currentTime 대신
      // framesDecoded를 쓰는 이유는 video 요소가 멈춰도 currentTime이
      // 남은 버퍼로 진행할 수 있기 때문이다.
      if (framesDecoded > lastFramesDecodedRef.current) {
        lastFramesDecodedRef.current = framesDecoded;
        lastFrameAtRef.current = now;
      } else if (lastFrameAtRef.current === null) {
        lastFrameAtRef.current = now;
      }

      const staleMs = lastFrameAtRef.current === null
        ? null
        : now - lastFrameAtRef.current;

      // 지연 추정: jitter buffer 지연 + RTT 절반.
      // 정확한 glass-to-glass 지연이 아니다. VID-01의 규범 측정은
      // 32-9가 정한 "타임코드가 보이는 원본 촬영"이다.
      let latencyMs: number | null = null;
      let receiveLatencyMs: number | null = null;
      if (jitterDelayMs !== null) {
        latencyMs = jitterDelayMs;
        // 수신 지연 = 네트워크 편도 + 수신부터 디코딩까지.
        // processingMs에 jitter buffer 대기가 포함되므로 그것을 쓰고,
        // 없으면 jitter buffer 값으로 대체한다.
        const pipeline = processingMs ?? jitterDelayMs;
        receiveLatencyMs = pipeline + (rttMs !== null ? rttMs / 2 : 0);
        latencyHistoryRef.current.push({ at: now, estimateMs: receiveLatencyMs });
        const cutoff = now - DEGRADED_WINDOW_MS * 4;
        latencyHistoryRef.current = latencyHistoryRef.current.filter(
          (sample) => sample.at >= cutoff,
        );
      }

      const stalledWarn = staleMs !== null && staleMs >= STALL_WARN_MS;
      const stalledBlock = staleMs !== null && staleMs >= STALL_BLOCK_MS;

      // DEGRADED 판정(32-4): 최근 5초 평균 지연 증가, 3초 이상 정지,
      // 연속 프레임 손실 중 하나.
      const recent = latencyHistoryRef.current.filter(
        (sample) => sample.at >= now - DEGRADED_WINDOW_MS,
      );
      const older = latencyHistoryRef.current.filter(
        (sample) => sample.at < now - DEGRADED_WINDOW_MS,
      );
      const mean = (list: LatencySample[]) =>
        list.length === 0
          ? null
          : list.reduce((sum, s) => sum + s.estimateMs, 0) / list.length;
      const recentMean = mean(recent);
      const olderMean = mean(older);
      const latencyGrowing =
        recentMean !== null && olderMean !== null && recentMean > olderMean * 1.5;

      const degraded = stalledWarn || latencyGrowing;

      setStatus((prev) => ({
        ...prev,
        state: degraded ? "DEGRADED" : "LIVE",
        staleMs,
        stalledWarn,
        stalledBlock,
        receiveLatencyMs,
        latencyMs,
        processingMs,
        framesDecoded,
        fps,
        width,
        height,
        // 비트레이트는 증분이 없는 주기에 null이 되므로 이전 값을 유지한다.
        bitrateKbps: bitrateKbps ?? prev.bitrateKbps,
        lossPct,
        packetsLost,
        freezeCount,
        freezeSeconds,
        rttMs,
        jitterMs,
      }));
    }, STATS_INTERVAL_MS);

    return () => clearInterval(id);
  }, [enabled]);

  /**
   * 탭이 다시 보이면 즉시 재연결한다.
   *
   * 브라우저는 숨겨진 탭의 setInterval을 크게 늦춘다(Chrome은 1분에 1회
   * 수준). 그래서 백그라운드에서는 정체 감시 타이머를 신뢰할 수 없고,
   * 탭으로 돌아왔을 때 한참 동안 끊긴 화면이 남는다. 실제로 서버가 복구된
   * 뒤에도 브라우저가 재시도하지 않는 상태를 관측했다.
   *
   * 조종자가 탭을 보는 순간이 영상이 필요한 순간이므로 그때 바로 붙인다.
   */
  useEffect(() => {
    if (!enabled) return;

    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const pc = pcRef.current;
      if (pc && pc.connectionState === "connected") return;

      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      retryRef.current = 0;
      lastAttemptAtRef.current = Date.now();
      patch({ state: "CONNECTING", error: null });
      setConnectTick((tick) => tick + 1);
    };

    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [enabled, patch]);

  /** VID-01 측정용. 누적된 지연 추정 표본을 돌려준다. */
  const latencySamples = useCallback(() => [...latencyHistoryRef.current], []);

  const reconnectNow = useCallback(() => {
    retryRef.current = 0;
    teardown();
    setConnectTick((tick) => tick + 1);
  }, [teardown]);

  return { attachVideo, status, latencySamples, reconnectNow };
}

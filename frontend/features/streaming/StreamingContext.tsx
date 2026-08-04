"use client";

import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  useWhepStream,
  type LatencySample,
  type StreamPath,
  type WhepStatus,
} from "./useWhepStream";

/**
 * 스트림 연결을 앱 전체에서 한 번만 유지한다 (S15P11A301-107).
 *
 * 두 소비자가 각자 useWhepStream을 호출하면 WHEP 세션이 두 개
 * 열려 MediaMTX에 중복 연결된다. 연결은 여기서 한 번만 만들고 상태를 공유한다.
 *
 * 조종 UI가 이 상태를 보던 이유는 SR-010이다. 영상이 3초 이상 멈추면
 * 신규 수동 전진 명령을 보내지 않아야 한다.
 */

interface StreamingContextValue {
  attachVideo: (element: HTMLVideoElement | null) => void;
  status: WhepStatus;
  latencySamples: () => LatencySample[];
  reconnectNow: () => void;
  path: StreamPath;
  setPath: (path: StreamPath) => void;
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
}

const StreamingContext = createContext<StreamingContextValue | null>(null);

export function StreamingProvider({ children }: { children: ReactNode }) {
  // 자동 전환은 지연이 갑자기 달라져 수동 조작 판단을 흐릴 수 있으므로
  // MVP에서 구현하지 않는다. 운영자가 직접 선택한다(32-4).
  const [path, setPath] = useState<StreamPath>("LOCAL");
  const [enabled, setEnabled] = useState(true);

  const { attachVideo, status, latencySamples, reconnectNow } = useWhepStream(
    enabled,
    path,
  );

  const value = useMemo(
    () => ({
      attachVideo,
      status,
      latencySamples,
      reconnectNow,
      path,
      setPath,
      enabled,
      setEnabled,
    }),
    [attachVideo, status, latencySamples, reconnectNow, path, enabled],
  );

  return (
    <StreamingContext.Provider value={value}>
      {children}
    </StreamingContext.Provider>
  );
}

export function useStreaming() {
  const ctx = useContext(StreamingContext);
  if (!ctx) {
    throw new Error("useStreaming must be used inside StreamingProvider");
  }
  return ctx;
}

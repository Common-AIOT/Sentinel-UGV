"use client";

import type { CSSProperties, ReactNode } from "react";
import { Toaster } from "sonner";
import { RobotProvider } from "@/features/robot/RobotContext";
import { StreamingProvider } from "@/features/streaming/StreamingContext";
import { SIDEBAR_WIDTH } from "@/lib/layout";

/** 토스트와 사이드바 경계 사이 여백. 양쪽에 같은 값이 들어간다. */
const TOAST_MARGIN = 12;

export function Providers({ children }: { children: ReactNode }) {
  return (
    <RobotProvider>
      {/* GamepadProvider는 뺐다 (S15P11A301-196) — 수동 조종 UI가 없어 소비자가
          없다. 조이스틱(S15P11A301-39)이 붙으면 이 자리에 되살린다. */}
      <StreamingProvider>{children}</StreamingProvider>
      {/* 토스트를 우측 사이드바 **안에** 가둔다 (S15P11A301-318).
          sonner 기본값은 폭 356px·여백 24px 이라 260px 사이드바를 왼쪽으로 120px
          넘어섰다. 넘어선 만큼은 영상 위를 덮는다 — 「수동 조종으로 전환합니다」
          처럼 설명이 붙는 토스트가 특히 컸다.
          폭만 260px 으로 줄여도 여백 24px 때문에 여전히 24px 이 삐져나온다.
          그래서 여백을 12px 로 좁히고 폭에서 양쪽 여백을 뺀다 — 12px + 236px +
          12px = 260px 으로 사이드바에 정확히 들어간다.
          `--width` 는 sonner 가 토스트 폭에 그대로 쓰는 변수다(dist/styles.css). */}
      <Toaster
        position="bottom-right"
        offset={TOAST_MARGIN}
        style={{ "--width": `${SIDEBAR_WIDTH - TOAST_MARGIN * 2}px` } as CSSProperties}
        toastOptions={{
          style: {
            background: "#1f2733",
            border: "1px solid rgba(255,255,255,0.09)",
            color: "#e6ecf2",
            fontFamily: "inherit",
            fontSize: "12px",
          },
        }}
      />
    </RobotProvider>
  );
}

"use client";

import type { ReactNode } from "react";
import { Toaster } from "sonner";
import { RobotProvider } from "@/features/robot/RobotContext";
import { StreamingProvider } from "@/features/streaming/StreamingContext";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <RobotProvider>
      {/* GamepadProvider는 뺐다 (S15P11A301-196) — 수동 조종 UI가 없어 소비자가
          없다. 조이스틱(S15P11A301-39)이 붙으면 이 자리에 되살린다. */}
      <StreamingProvider>{children}</StreamingProvider>
      <Toaster
        position="bottom-right"
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

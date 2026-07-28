"use client";

import type { ReactNode } from "react";
import { Toaster } from "sonner";
import { RobotProvider } from "@/features/robot/RobotContext";
import { StreamingProvider } from "@/features/streaming/StreamingContext";
import { GamepadProvider } from "@/features/control/GamepadContext";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <RobotProvider>
      <StreamingProvider>
        <GamepadProvider>{children}</GamepadProvider>
      </StreamingProvider>
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

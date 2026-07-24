"use client";

import type { ReactNode } from "react";
import { Toaster } from "sonner";
import { RobotProvider } from "@/features/robot/RobotContext";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <RobotProvider>
      {children}
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

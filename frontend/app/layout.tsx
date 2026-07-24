import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Disaster Robot GCS",
  description:
    "Real-time control and monitoring of disaster exploration robots with live maps, video feeds, sensor data, and command interfaces for efficient rescue operations.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

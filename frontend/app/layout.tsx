import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "SENTINEL-UGV 관제",
  description:
    "Real-time control and monitoring of disaster exploration robots with live maps, video feeds, sensor data, and command interfaces for efficient rescue operations.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <head>
        {/*
          라틴 서브셋만 미리 받는다 (S15P11A301-302). 한글 서브셋은 914KB라
          preload 하면 첫 페인트 대역을 잡아먹는다 — @font-face 의 swap 이
          알아서 처리하게 두고, 숫자·라벨이 먼저 뜨는 쪽을 택했다.
        */}
        <link
          rel="preload"
          href="/fonts/AstaSans-latin.woff2"
          as="font"
          type="font/woff2"
          crossOrigin="anonymous"
        />
        <link
          rel="preload"
          href="/fonts/D2Coding-mono.woff2"
          as="font"
          type="font/woff2"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

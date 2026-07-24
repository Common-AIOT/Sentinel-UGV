"use client";

import { useState, useRef, useEffect } from "react";
import { Signal, Settings, ArrowLeftRight } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";

function useMockStream(quality: "1080p" | "720p", enabled: boolean) {
  const streamRef = useRef<MediaStream | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) { setConnected(false); return; }

    let cancelled = false;
    const canvas = document.createElement("canvas");
    canvas.width = quality === "1080p" ? 1920 : 1280;
    canvas.height = quality === "1080p" ? 1080 : 720;
    const ctx = canvas.getContext("2d")!;
    let frame = 0;

    const render = () => {
      if (cancelled) return;
      frame++;
      const t = frame / 30;

      ctx.fillStyle = "#10161d";
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.strokeStyle = "rgba(255,255,255,0.035)";
      ctx.lineWidth = 1;
      for (let y = 0; y < canvas.height; y += 40) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
      }
      for (let x = 0; x < canvas.width; x += 40) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
      }

      ctx.fillStyle = "rgba(255,255,255,0.04)";
      ctx.fillRect(canvas.width * 0.25, canvas.height * 0.2, canvas.width * 0.5, canvas.height * 0.6);

      for (let i = 0; i < 30; i++) {
        const px = (Math.sin(t * 2.1 + i * 1.7) * 0.5 + 0.5) * canvas.width;
        const py = (Math.cos(t * 1.3 + i * 2.3) * 0.5 + 0.5) * canvas.height;
        ctx.fillStyle = `rgba(230,236,242,${0.07 + Math.sin(t + i) * 0.03})`;
        ctx.beginPath();
        ctx.arc(px, py, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }

      ctx.fillStyle = "rgba(255,255,255,0.05)";
      ctx.fillRect(0, (frame * 2) % canvas.height, canvas.width, 2);

      ctx.fillStyle = "rgba(230,236,242,0.6)";
      ctx.font = `bold ${canvas.width * 0.007}px JetBrains Mono`;
      ctx.fillText(`로봇캠  ${quality}`, canvas.width * 0.01, canvas.height * 0.04);
      ctx.font = `${canvas.width * 0.006}px JetBrains Mono`;
      ctx.fillStyle = "rgba(230,236,242,0.38)";
      ctx.fillText(new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC", canvas.width * 0.01, canvas.height * 0.07);
      ctx.fillText(`녹화 ●`, canvas.width * 0.87, canvas.height * 0.04);

      requestAnimationFrame(render);
    };

    const timer = setTimeout(() => {
      if (cancelled) return;
      requestAnimationFrame(render);
      const stream = canvas.captureStream(30);
      streamRef.current = stream;
      setConnected(true);
    }, 1200);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      streamRef.current = null;
      setConnected(false);
    };
  }, [enabled, quality]);

  return { streamRef, connected };
}

function VideoDisplay({ streamRef, className }: { streamRef: React.RefObject<MediaStream | null>; className?: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return;
    if (streamRef.current) {
      el.srcObject = streamRef.current;
      el.play().catch(() => {});
    }
    const id = setInterval(() => {
      if (streamRef.current && el.srcObject !== streamRef.current) {
        el.srcObject = streamRef.current;
        el.play().catch(() => {});
      }
    }, 200);
    return () => clearInterval(id);
  }, [streamRef]);

  return <video ref={videoRef} autoPlay muted playsInline className={className} />;
}

interface VideoPanelProps {
  isMain?: boolean;
  onSwap: () => void;
}

export default function VideoPanel({ isMain = false, onSwap }: VideoPanelProps) {
  const { videoQuality, setVideoQuality, videoConnected } = useRobot();
  const [showSettings, setShowSettings] = useState(false);
  const { streamRef, connected } = useMockStream(videoQuality, videoConnected);

  return (
    <div
      className={`relative bg-[#10161d] border-b border-border flex-shrink-0 group ${isMain ? "flex-1 min-h-0" : ""}`}
      style={isMain ? {} : { height: 180 }}
    >
      <VideoDisplay streamRef={streamRef} className="w-full h-full object-cover" />

      {!connected && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#10161d] gap-2">
          <Signal size={isMain ? 28 : 20} className="text-destructive opacity-60" />
          <span className="font-mono text-[10px] text-destructive/60 tracking-widest">신호 없음</span>
          <span className="font-mono text-[9px] text-muted-foreground">WebRTC 연결 끊김</span>
        </div>
      )}

      <div className="absolute top-1.5 left-1.5 flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-primary animate-pulse" : "bg-destructive"}`} />
        <span className="font-mono text-[9px] text-muted-foreground bg-black/50 px-1 rounded">
          {connected ? "실시간" : "오프라인"} · {videoQuality}
        </span>
      </div>

      <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => setShowSettings(s => !s)}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title="화질 설정"
        >
          <Settings size={11} />
        </button>
        <button
          onClick={onSwap}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title={isMain ? "사이드바로" : "메인으로"}
        >
          <ArrowLeftRight size={11} />
        </button>
      </div>

      {showSettings && (
        <div className="absolute top-8 right-1.5 bg-card border border-border rounded p-2 z-10 space-y-1 min-w-[130px]">
          <p className="font-mono text-[9px] text-muted-foreground uppercase tracking-wider mb-1.5">영상 화질</p>
          {(["1080p", "720p"] as const).map(q => (
            <button
              key={q}
              onClick={() => { setVideoQuality(q); setShowSettings(false); }}
              className={`w-full text-left font-mono text-[10px] px-2 py-1 rounded transition-colors ${
                videoQuality === q ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              }`}
            >
              {q}{videoQuality === q && " ✓"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

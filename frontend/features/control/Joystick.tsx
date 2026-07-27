"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRobot } from "@/features/robot/RobotContext";
import { useStreaming } from "@/features/streaming/StreamingContext";
import { Gamepad2, Usb, AlertTriangle } from "lucide-react";

const RADIUS = 52;
const KNOB_R = 18;

// ── Gamepad API 훅 ────────────────────────────────────────────────────────
function useGamepad() {
  const [gamepadConnected, setGamepadConnected] = useState(false);
  const [gamepadName, setGamepadName] = useState("");
  const axesRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const onConnect = (e: GamepadEvent) => {
      setGamepadConnected(true);
      setGamepadName(e.gamepad.id.slice(0, 30));
    };
    const onDisconnect = () => {
      setGamepadConnected(false);
      setGamepadName("");
      axesRef.current = { x: 0, y: 0 };
    };

    window.addEventListener("gamepadconnected", onConnect);
    window.addEventListener("gamepaddisconnected", onDisconnect);

    // 폴링: 브라우저는 gamepad 상태를 이벤트가 아닌 폴링으로만 읽음
    let raf: number;
    const poll = () => {
      const pads = navigator.getGamepads();
      for (const pad of pads) {
        if (!pad) continue;
        // 왼쪽 아날로그 스틱: axes[0]=좌우, axes[1]=상하
        const x = Math.abs(pad.axes[0]) > 0.08 ? pad.axes[0] : 0;
        const y = Math.abs(pad.axes[1]) > 0.08 ? -pad.axes[1] : 0; // 위가 +
        axesRef.current = {
          x: +x.toFixed(3),
          y: +y.toFixed(3),
        };
        if (!gamepadConnected) {
          setGamepadConnected(true);
          setGamepadName(pad.id.slice(0, 30));
        }
        break;
      }
      raf = requestAnimationFrame(poll);
    };
    raf = requestAnimationFrame(poll);

    return () => {
      window.removeEventListener("gamepadconnected", onConnect);
      window.removeEventListener("gamepaddisconnected", onDisconnect);
      cancelAnimationFrame(raf);
    };
  }, [gamepadConnected]);

  return { gamepadConnected, gamepadName, axesRef };
}

// ── 조이스틱 컴포넌트 ────────────────────────────────────────────────────
export default function Joystick() {
  const { sendControl, wsConnected } = useRobot();
  const { gamepadConnected, gamepadName, axesRef } = useGamepad();
  // SR-010: 관제 영상이 3초 이상 멈추면 신규 수동 전진 명령을 보내지 않는다.
  // 조종자가 보이지 않는 상황에서 로봇을 움직이게 되기 때문이다.
  // 이미 전송된 주행 명령은 Jetson의 300ms TTL이 정지시킨다.
  const { status: streamStatus } = useStreaming();
  const videoBlocked = streamStatus.stalledBlock;

  // 화면 표시용 위치 (물리 or 마우스)
  const [displayPos, setDisplayPos] = useState({ x: 0, y: 0 });
  const [mouseActive, setMouseActive] = useState(false);
  const [safetyTripped, setSafetyTripped] = useState(false);
  const [disabled, setDisabled] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const mousePosRef = useRef({ x: 0, y: 0 });
  const safetyTimer = useRef<ReturnType<typeof setTimeout>>();

  // 25Hz 전송 루프 — 물리 연결 시 gamepad 우선, 아니면 마우스
  useEffect(() => {
    const id = setInterval(() => {
      const src = gamepadConnected ? axesRef.current : mousePosRef.current;
      setDisplayPos({ x: src.x * RADIUS, y: -src.y * RADIUS });
      if (disabled) return;
      // 영상이 멈춘 동안에는 전진(y > 0)만 막고 정지·후진은 허용한다.
      // 전진을 0으로 클램프해야 이미 밀고 있던 스틱도 멈춘다.
      const forwardBlocked = videoBlocked && src.y > 0;
      sendControl(src.x, forwardBlocked ? 0 : src.y);
    }, 40);
    return () => clearInterval(id);
  }, [gamepadConnected, disabled, videoBlocked, sendControl, axesRef]);

  // 안전 차단 — 300ms 응답 없음
  const resetSafety = useCallback(() => {
    clearTimeout(safetyTimer.current);
    if (!wsConnected) return;
    safetyTimer.current = setTimeout(() => {
      if (dragging.current) {
        sendControl(0, 0);
        setDisabled(true);
        setSafetyTripped(true);
        dragging.current = false;
        setMouseActive(false);
        mousePosRef.current = { x: 0, y: 0 };
      }
    }, 300);
  }, [wsConnected, sendControl]);

  // 마우스 드래그 핸들러 (물리 조이스틱 없을 때만 유효)
  const getRelPos = (clientX: number, clientY: number) => {
    const el = containerRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    let dx = clientX - (rect.left + rect.width / 2);
    let dy = clientY - (rect.top + rect.height / 2);
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > RADIUS) { dx = dx / dist * RADIUS; dy = dy / dist * RADIUS; }
    return { x: +(dx / RADIUS).toFixed(3), y: +(-dy / RADIUS).toFixed(3) };
  };

  const handleStart = (cx: number, cy: number) => {
    if (disabled || gamepadConnected) return;
    dragging.current = true;
    setMouseActive(true);
    setSafetyTripped(false);
    mousePosRef.current = getRelPos(cx, cy);
    resetSafety();
  };
  const handleMove = (cx: number, cy: number) => {
    if (!dragging.current) return;
    mousePosRef.current = getRelPos(cx, cy);
    resetSafety();
  };
  const handleEnd = () => {
    dragging.current = false;
    setMouseActive(false);
    mousePosRef.current = { x: 0, y: 0 };
    sendControl(0, 0);
    clearTimeout(safetyTimer.current);
  };

  const cx = RADIUS + KNOB_R + 4;
  const cy = RADIUS + KNOB_R + 4;
  const totalSize = (RADIUS + KNOB_R + 4) * 2;
  const isActive = gamepadConnected
    ? (Math.abs(axesRef.current.x) > 0.08 || Math.abs(axesRef.current.y) > 0.08)
    : mouseActive;

  return (
    <div className="flex flex-col items-center gap-2">
      {/* 상태 표시줄 */}
      <div className="flex items-center gap-3">
        {gamepadConnected ? (
          <div className="flex items-center gap-1.5 bg-primary/10 border border-primary/25 rounded px-2 py-0.5">
            <Gamepad2 size={11} className="text-primary" />
            <span className="text-[10px] font-medium text-primary">물리 조이스틱 연결됨</span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 bg-muted border border-border rounded px-2 py-0.5">
            <Usb size={11} className="text-muted-foreground" />
            <span className="text-[10px] text-muted-foreground">조이스틱 미연결 — 마우스 사용</span>
          </div>
        )}
        {safetyTripped && (
          <div className="flex items-center gap-1 text-destructive">
            <AlertTriangle size={10} />
            <span className="text-[10px] font-medium">안전 차단</span>
          </div>
        )}
        {videoBlocked && (
          <div className="flex items-center gap-1 text-destructive">
            <AlertTriangle size={10} />
            <span className="text-[10px] font-medium">영상 정지 · 전진 차단</span>
          </div>
        )}
        {disabled && (
          <button onClick={() => { setDisabled(false); setSafetyTripped(false); }} className="text-[10px] text-accent underline">
            초기화
          </button>
        )}
      </div>

      {/* 게임패드 이름 */}
      {gamepadConnected && gamepadName && (
        <p className="font-mono text-[8px] text-muted-foreground/60 max-w-[200px] truncate text-center">{gamepadName}</p>
      )}

      {/* 조이스틱 SVG */}
      <div
        ref={containerRef}
        className={`relative select-none touch-none ${disabled ? "opacity-40 pointer-events-none" : ""} ${gamepadConnected ? "cursor-not-allowed" : "cursor-grab active:cursor-grabbing"}`}
        style={{ width: totalSize, height: totalSize }}
        onMouseDown={e => handleStart(e.clientX, e.clientY)}
        onMouseMove={e => handleMove(e.clientX, e.clientY)}
        onMouseUp={handleEnd}
        onMouseLeave={handleEnd}
        onTouchStart={e => { e.preventDefault(); handleStart(e.touches[0].clientX, e.touches[0].clientY); }}
        onTouchMove={e => { e.preventDefault(); handleMove(e.touches[0].clientX, e.touches[0].clientY); }}
        onTouchEnd={handleEnd}
      >
        <svg width={totalSize} height={totalSize}>
          {/* 베이스 */}
          <circle cx={cx} cy={cy} r={RADIUS} fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.16)" strokeWidth="1" />
          <circle cx={cx} cy={cy} r={RADIUS * 0.6} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" strokeDasharray="3,3" />
          <circle cx={cx} cy={cy} r={RADIUS * 0.25} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
          <line x1={cx - RADIUS} y1={cy} x2={cx + RADIUS} y2={cy} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
          <line x1={cx} y1={cy - RADIUS} x2={cx} y2={cy + RADIUS} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />

          {/* 선 */}
          {isActive && (
            <line x1={cx} y1={cy} x2={cx + displayPos.x} y2={cy + displayPos.y}
              stroke="rgba(69,201,140,0.45)" strokeWidth="1" strokeDasharray="2,2" />
          )}

          {/* 노브 */}
          <circle
            cx={cx + displayPos.x} cy={cy + displayPos.y} r={KNOB_R}
            fill={isActive ? "rgba(69,201,140,0.28)" : "rgba(255,255,255,0.06)"}
            stroke={isActive ? "rgba(69,201,140,0.9)" : "rgba(255,255,255,0.28)"}
            strokeWidth="1.5"
            style={{ transition: gamepadConnected ? "cx 0.04s, cy 0.04s" : "none" }}
          />
          <circle cx={cx + displayPos.x} cy={cy + displayPos.y} r={5}
            fill={isActive ? "rgba(69,201,140,0.95)" : "rgba(255,255,255,0.32)"}
            style={{ transition: gamepadConnected ? "cx 0.04s, cy 0.04s" : "none" }}
          />

          {/* 물리 조이스틱 표시 */}
          {gamepadConnected && (
            <text x={cx} y={cy + RADIUS + 14} textAnchor="middle" fontSize="8" fill="rgba(230,236,242,0.4)" fontFamily="JetBrains Mono">
              GAMEPAD
            </text>
          )}
        </svg>
      </div>

      {/* 수치 */}
      <div className="flex gap-4 font-mono text-[10px]">
        <span className="text-muted-foreground">X: <span className="text-primary">{(gamepadConnected ? axesRef.current.x : mousePosRef.current.x).toFixed(2)}</span></span>
        <span className="text-muted-foreground">Y: <span className="text-primary">{(gamepadConnected ? axesRef.current.y : mousePosRef.current.y).toFixed(2)}</span></span>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useRef, useState } from "react";
import { useRobot } from "@/features/robot/RobotContext";
import { useStreaming } from "@/features/streaming/StreamingContext";
import { useGamepad } from "./GamepadContext";
import { Gamepad2, Usb, AlertTriangle } from "lucide-react";

const RADIUS = 46;
const KNOB_R = 15;

/**
 * 수동 조종 입력 표시.
 *
 * 화면 조이스틱을 마우스로 끄는 기능은 두지 않는다. 스프링 복귀가 없고 포인터가
 * 요소를 벗어나면 입력이 끊겨서 차량 조종에 쓸 수 있는 정밀도가 나오지 않는다.
 * 명세 1.x 리스크 대응표가 "조이스틱 연결 해제 → 즉시 0 속도, MANUAL 종료,
 * PAUSED 전환"을 규정하는데, 마우스 대체 수단이 있으면 이 규칙 자체가 성립하지
 * 않는다. 즉 물리 게임패드가 수동 조종의 유일한 입력이다.
 *
 * 따라서 이 컴포넌트는 물리 스틱의 축 값을 보여주는 읽기 전용 계기다.
 */
export default function Joystick() {
  const { sendControl, sendCommand, status } = useRobot();
  const { connected, name, axesRef, active } = useGamepad();
  // SR-010: 관제 영상이 3초 이상 멈추면 신규 수동 전진 명령을 보내지 않는다.
  // 조종자가 보이지 않는 상황에서 로봇을 움직이게 되기 때문이다.
  // 이미 전송된 주행 명령은 Jetson의 300ms TTL이 정지시킨다.
  const { status: streamStatus } = useStreaming();
  const videoBlocked = streamStatus.stalledBlock;

  const [displayPos, setDisplayPos] = useState({ x: 0, y: 0 });
  const manual = status.controlMode === "MANUAL";

  // 25Hz 전송 루프. 수동 모드이고 게임패드가 붙어 있을 때만 명령을 보낸다.
  useEffect(() => {
    const id = setInterval(() => {
      const { x, y } = axesRef.current;
      setDisplayPos({ x: x * RADIUS, y: -y * RADIUS });
      if (!manual || !connected) return;
      // 영상이 멈춘 동안에는 전진(y > 0)만 막고 정지·후진은 허용한다.
      // 전진을 0으로 클램프해야 이미 밀고 있던 스틱도 멈춘다.
      sendControl(x, videoBlocked && y > 0 ? 0 : y);
    }, 40);
    return () => clearInterval(id);
  }, [manual, connected, videoBlocked, sendControl, axesRef]);

  // 연결 해제 처리. 명세 1.x: 즉시 0 속도 명령 → MANUAL 종료 → PAUSED 전환 → 경고.
  const wasConnected = useRef(connected);
  useEffect(() => {
    const lost = wasConnected.current && !connected;
    wasConnected.current = connected;
    if (!lost) return;
    sendControl(0, 0);
    if (status.controlMode === "MANUAL") sendCommand("auto");
  }, [connected, sendControl, sendCommand, status.controlMode]);

  const cx = RADIUS + KNOB_R + 3;
  const cy = RADIUS + KNOB_R + 3;
  const total = (RADIUS + KNOB_R + 3) * 2;

  if (!connected) {
    return (
      <div className="flex items-center gap-2.5">
        <Usb size={16} className="text-accent flex-shrink-0" />
        <div>
          <p className="text-xs font-medium text-accent">게임패드를 연결하세요</p>
          <p className="text-[11px] text-muted-foreground">
            수동 조종은 물리 게임패드로만 가능합니다
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-4">
      <div className="relative flex-shrink-0" style={{ width: total, height: total }}>
        <svg width={total} height={total} role="img" aria-label="게임패드 입력 표시">
          <circle cx={cx} cy={cy} r={RADIUS} fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.16)" strokeWidth="1" />
          <circle cx={cx} cy={cy} r={RADIUS * 0.6} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" strokeDasharray="3,3" />
          <line x1={cx - RADIUS} y1={cy} x2={cx + RADIUS} y2={cy} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
          <line x1={cx} y1={cy - RADIUS} x2={cx} y2={cy + RADIUS} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
          {active && (
            <line
              x1={cx} y1={cy} x2={cx + displayPos.x} y2={cy + displayPos.y}
              stroke="rgba(69,201,140,0.45)" strokeWidth="1" strokeDasharray="2,2"
            />
          )}
          <circle
            cx={cx + displayPos.x} cy={cy + displayPos.y} r={KNOB_R}
            fill={active ? "rgba(69,201,140,0.28)" : "rgba(255,255,255,0.06)"}
            stroke={active ? "rgba(69,201,140,0.9)" : "rgba(255,255,255,0.28)"}
            strokeWidth="1.5"
            style={{ transition: "cx 0.04s, cy 0.04s" }}
          />
        </svg>
      </div>

      <div className="flex flex-col items-start gap-1 min-w-0">
        <div className="flex items-center gap-1.5 bg-primary/10 border border-primary/25 rounded px-2 py-0.5">
          <Gamepad2 size={11} className="text-primary" />
          <span className="text-[11px] font-medium text-primary">연결됨</span>
        </div>
        {name && (
          <p className="font-mono text-[11px] text-muted-foreground/70 max-w-[180px] truncate" title={name}>
            {name}
          </p>
        )}
        <div className="flex gap-3 font-mono text-[11px] tabular-nums">
          <span className="text-muted-foreground">X <span className="text-primary">{axesRef.current.x.toFixed(2)}</span></span>
          <span className="text-muted-foreground">Y <span className="text-primary">{axesRef.current.y.toFixed(2)}</span></span>
        </div>
        {videoBlocked && manual && (
          <div className="flex items-center gap-1 text-destructive">
            <AlertTriangle size={11} />
            <span className="text-[11px] font-medium">영상 정지 · 전진 차단</span>
          </div>
        )}
      </div>
    </div>
  );
}

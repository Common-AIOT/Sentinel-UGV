"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

/**
 * 게임패드 연결 상태와 축 값의 단일 출처.
 *
 * StreamingContext와 같은 이유로 Context다. 페이지(모드 전환 허용 여부)와 조종
 * 패널(축 시각화)이 각자 훅을 호출하면 requestAnimationFrame 폴링 루프가 두 개
 * 돌아간다.
 *
 * 축 값은 state가 아니라 ref에 담는다. 25Hz 전송 루프와 60Hz 폴링이 매 프레임
 * setState를 하면 트리 전체가 리렌더된다.
 */

const DEADZONE = 0.08;

interface GamepadContextValue {
  connected: boolean;
  name: string;
  axesRef: React.MutableRefObject<{ x: number; y: number }>;
  /** 데드존을 넘는 입력이 있는지. 표시용이며 60Hz로 갱신하지 않는다. */
  active: boolean;
}

const GamepadCtx = createContext<GamepadContextValue | null>(null);

export function GamepadProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [name, setName] = useState("");
  const [active, setActive] = useState(false);
  const axesRef = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const onConnect = (e: GamepadEvent) => {
      setConnected(true);
      setName(e.gamepad.id.slice(0, 40));
    };
    const onDisconnect = () => {
      setConnected(false);
      setName("");
      setActive(false);
      axesRef.current = { x: 0, y: 0 };
    };

    window.addEventListener("gamepadconnected", onConnect);
    window.addEventListener("gamepaddisconnected", onDisconnect);

    // 브라우저는 gamepad 상태를 이벤트가 아닌 폴링으로만 노출한다.
    let raf = 0;
    let lastActive = false;
    const poll = () => {
      const pads = navigator.getGamepads?.() ?? [];
      let found = false;
      for (const pad of pads) {
        if (!pad) continue;
        found = true;
        // 왼쪽 아날로그 스틱. axes[1]은 위가 음수라 부호를 뒤집어 위를 +로 둔다.
        const x = Math.abs(pad.axes[0]) > DEADZONE ? pad.axes[0] : 0;
        const y = Math.abs(pad.axes[1]) > DEADZONE ? -pad.axes[1] : 0;
        axesRef.current = { x: +x.toFixed(3), y: +y.toFixed(3) };
        const isActive = x !== 0 || y !== 0;
        if (isActive !== lastActive) {
          lastActive = isActive;
          setActive(isActive);
        }
        break;
      }
      // gamepadconnected 이벤트를 놓친 경우(탭 복귀 등)를 폴링으로 보정한다.
      setConnected(prev => (prev === found ? prev : found));
      raf = requestAnimationFrame(poll);
    };
    raf = requestAnimationFrame(poll);

    return () => {
      window.removeEventListener("gamepadconnected", onConnect);
      window.removeEventListener("gamepaddisconnected", onDisconnect);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <GamepadCtx.Provider value={{ connected, name, axesRef, active }}>
      {children}
    </GamepadCtx.Provider>
  );
}

export function useGamepad() {
  const ctx = useContext(GamepadCtx);
  if (!ctx) throw new Error("useGamepad must be used inside GamepadProvider");
  return ctx;
}

/**
 * 게임패드가 새로 연결됐을 때 한 번 호출한다.
 * 연결을 곧바로 모드 전환으로 해석하지 않기 위해 "연결됨" 전이만 알린다.
 */
export function useOnGamepadConnect(handler: () => void) {
  const { connected } = useGamepad();
  const prev = useRef(connected);
  const cb = useCallback(handler, [handler]);
  useEffect(() => {
    if (connected && !prev.current) cb();
    prev.current = connected;
  }, [connected, cb]);
}

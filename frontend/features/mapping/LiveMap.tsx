"use client";

import { useEffect, useRef, useState } from "react";
import {
  startMapClient,
  type MapClientState,
} from "@/lib/foxgloveMapClient";
import { classifyGridCell, type OccupancyGrid } from "@/lib/occupancyGrid";
import { OverlayLine, OverlayStack } from "@/features/telemetry/PanelOverlay";
import { COLOR_FREE, COLOR_OCCUPIED, COLOR_UNKNOWN } from "./palette";

/**
 * 실시간 SLAM 지도 (S15P11A301-227).
 *
 * 젯슨의 `foxglove_bridge`에 직접 붙어 `/map`을 받는다. Foxglove Studio가 보는 것과
 * 같은 데이터·같은 주기(2초)다. 백엔드를 거치지 않는다 — telemetry 스키마에 격자
 * 필드가 없고 지도 조회 API는 임무 종료 후 PGM뿐이라, 그 둘로는 실시간이 안 된다.
 *
 * 임무 이력의 지도(S15P11A301-203)와 값 체계가 다르다. 그쪽은 PGM 바이트,
 * 이쪽은 nav2 `int8`이다. 판정은 `classifyGridCell`이 하고 **색은 공유한다**
 * (`palette.ts`).
 *
 * ## 같은 네트워크에서만 보인다
 *
 * `jetson.sentinel-ugv.xyz`가 젯슨의 LAN IP를 가리킨다. 외부망에서는 지도도 영상도
 * 나오지 않는다 — 영상(WHEP)이 이미 같은 제약이고 명세가 LAN 시연을 기본으로
 * 정했다(요약표 12행, 32-4). 그래서 연결 실패를 "오류"로만 적지 않고 그 사실이
 * 읽히는 문구를 둔다.
 */

/** 기본 주소. bridge 는 wss 로 뜬다(S15P11A301-224) — 평문이면 브라우저가 막는다. */
const DEFAULT_URL = "wss://jetson.sentinel-ugv.xyz:8765";

const STATE_LABEL: Record<MapClientState, string> = {
  connecting: "연결 중",
  waiting: "지도 대기",
  streaming: "실시간",
  disconnected: "끊김",
};

const STATE_TONE: Record<MapClientState, "ok" | "warn" | "bad" | "idle"> = {
  connecting: "idle",
  waiting: "warn",
  streaming: "ok",
  disconnected: "bad",
};

/**
 * 격자를 오프스크린 캔버스로 만든다. 셀 하나가 1픽셀이고 확대는 그리는 쪽에서 한다.
 *
 * **행을 뒤집는다.** nav2 격자는 첫 행이 아래(원점이 좌하단)이고 캔버스는 첫 행이
 * 위다. 뒤집지 않으면 지도가 상하 반전된 채로 그려지는데, 방 모양이 대칭에 가까우면
 * 눈으로 알기 어렵다. 저장된 지도 쪽에서 같은 규칙을 실측으로 확인했다
 * (S15P11A301-171, 99.5% 일치).
 */
function renderGrid(grid: OccupancyGrid): HTMLCanvasElement | null {
  const canvas = document.createElement("canvas");
  canvas.width = grid.width;
  canvas.height = grid.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  const image = ctx.createImageData(grid.width, grid.height);
  for (let row = 0; row < grid.height; row += 1) {
    const src = row * grid.width;
    const dst = (grid.height - 1 - row) * grid.width;
    for (let col = 0; col < grid.width; col += 1) {
      const cell = classifyGridCell(grid.data[src + col]);
      const rgb =
        cell === "occupied"
          ? COLOR_OCCUPIED
          : cell === "free"
            ? COLOR_FREE
            : COLOR_UNKNOWN;
      const offset = (dst + col) * 4;
      image.data[offset] = rgb[0];
      image.data[offset + 1] = rgb[1];
      image.data[offset + 2] = rgb[2];
      image.data[offset + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

export default function LiveMap() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const gridRef = useRef<OccupancyGrid | null>(null);
  const [state, setState] = useState<MapClientState>("connecting");
  const [detail, setDetail] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ w: number; h: number; res: number } | null>(
    null,
  );

  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_MAP_WS_URL || DEFAULT_URL;
    return startMapClient(url, {
      onGrid: grid => {
        gridRef.current = grid;
        setMeta({ w: grid.width, h: grid.height, res: grid.resolution });
        draw();
      },
      onState: (next, why) => {
        setState(next);
        setDetail(why ?? null);
      },
    });
    // draw 는 ref 만 읽으므로 의존성에 넣지 않는다. 넣으면 매 렌더마다
    // 재연결한다 — 2초짜리 스트림에서 그것은 곧 영구 재연결이다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 마지막으로 받은 격자를 컨테이너 크기에 맞춰 다시 그린다. */
  const draw = () => {
    const grid = gridRef.current;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!grid || !canvas || !container) return;

    const offscreen = renderGrid(grid);
    const ctx = canvas.getContext("2d");
    if (!offscreen || !ctx) return;

    const box = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(box.width * dpr));
    canvas.height = Math.max(1, Math.floor(box.height * dpr));
    canvas.style.width = `${box.width}px`;
    canvas.style.height = `${box.height}px`;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = `rgb(${COLOR_UNKNOWN.join(",")})`;
    ctx.fillRect(0, 0, box.width, box.height);

    // 종횡비를 유지한다. 늘리면 벽 두께가 방향에 따라 달라져 지도를 잘못 읽는다.
    const scale = Math.min(box.width / grid.width, box.height / grid.height);
    const drawW = grid.width * scale;
    const drawH = grid.height * scale;
    // 셀 경계를 흐리지 않는다. 격자는 사진이 아니라 데이터다.
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(
      offscreen,
      (box.width - drawW) / 2,
      (box.height - drawH) / 2,
      drawW,
      drawH,
    );
  };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(container);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasGrid = meta !== null;

  return (
    <div ref={containerRef} className="relative size-full overflow-hidden">
      {hasGrid && <canvas ref={canvasRef} />}
      {!hasGrid && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 px-3 text-center">
          <span className="font-mono text-[10px] text-muted-foreground">
            {STATE_LABEL[state]}
          </span>
          {/* 실패 이유를 반드시 적는다. 빈 격자만 보이면 같은 네트워크가 아닌
              것인지, bridge 가 꺼진 것인지, SLAM 이 안 도는 것인지 구분할 수
              없다. */}
          {detail && (
            <span className="font-mono text-[9px] text-muted-foreground/70 leading-snug">
              {detail}
            </span>
          )}
        </div>
      )}
      <MapStatus state={state} meta={meta} detail={detail} />
    </div>
  );
}

/** 상태 줄. 영상 오버레이와 같은 형식을 쓴다(S15P11A301-200). */
function MapStatus({
  state,
  meta,
  detail,
}: {
  state: MapClientState;
  meta: { w: number; h: number; res: number } | null;
  detail: string | null;
}) {
  return (
    <OverlayStack>
      <OverlayLine
        kind="SLAM"
        tone={STATE_TONE[state]}
        title={
          detail ??
          "젯슨의 foxglove_bridge 에서 /map 을 직접 받는다. 같은 네트워크에서만 보인다."
        }
      >
        {meta
          ? `${STATE_LABEL[state]} · ${meta.w}×${meta.h} · ${meta.res.toFixed(2)}m/셀`
          : STATE_LABEL[state]}
      </OverlayLine>
    </OverlayStack>
  );
}
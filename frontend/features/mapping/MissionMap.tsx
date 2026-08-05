"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Map as MapIcon } from "lucide-react";
import { COLOR_FREE, COLOR_OCCUPIED, COLOR_UNKNOWN } from "./palette";
import { worldToPixel, type GridGeometry as Meta } from "@/lib/gridGeometry";
import {
  api,
  ApiError,
  type EncounterSummary,
  type MapView,
  type TrajectoryPoint,
} from "@/lib/api";

/**
 * 임무 지도 (S15P11A301-203). 젯슨이 올린 SLAM 지도(pgm) 위에 발견 마커와
 * 주행 궤적을 그린다. 휠 확대·드래그 이동·더블클릭 초기화를 지원한다.
 *
 * 좌표 변환은 조회 응답의 전정밀 메타데이터(#197)를 쓰고, 없으면 yaml 을 파싱한다
 * (yaml 의 origin 은 유효숫자 3자리 절단 — 최대 1픽셀 오차, 화면상 무의미).
 * pgm 은 브라우저가 <img> 로 못 읽는 P5 바이너리라 직접 파싱해 Canvas 에 그린다.
 *
 * 상하 반전: PGM 은 0행이 위, map 좌표계는 y 가 위로 증가 —
 * py = height - (y - originY) / resolution (실측 99.5% 일치로 확정된 규칙).
 */

interface Props {
  missionId: string;
  encounters: EncounterSummary[];
  onEncounterClick: (encounter: EncounterSummary) => void;
  /**
   * true 면 부모가 준 높이를 다 쓴다(S15P11A301-273 — 이력 화면이 지도를 메인으로).
   * 기본은 예전처럼 최대 420px — 높이가 콘텐츠를 따라가는 문맥(blackbox-experiment)에서
   * 컨테이너 높이를 재면 캔버스가 생기기 전 높이(≈0)를 읽는 순환이 생긴다.
   */
  fill?: boolean;
}

type MapState = "loading" | "no-map" | "pending-upload" | "ready" | "error";

interface Pgm {
  width: number;
  height: number;
  pixels: Uint8Array;
}


/** P5 헤더는 토큰 4개(P5·width·height·maxval). 길이를 상수로 박지 않고 # 주석을 허용한다. */
export function parsePgm(buf: ArrayBuffer): Pgm {
  const bytes = new Uint8Array(buf);
  let pos = 0;
  const isSpace = (c: number) => c === 32 || c === 9 || c === 10 || c === 13;
  const readToken = (): string => {
    while (pos < bytes.length) {
      if (bytes[pos] === 35 /* # */) {
        while (pos < bytes.length && bytes[pos] !== 10) pos++;
      } else if (isSpace(bytes[pos])) {
        pos++;
      } else break;
    }
    const start = pos;
    while (pos < bytes.length && !isSpace(bytes[pos])) pos++;
    return String.fromCharCode(...bytes.subarray(start, pos));
  };
  const magic = readToken();
  if (magic !== "P5") throw new Error(`P5 형식이 아닙니다: ${magic}`);
  const width = parseInt(readToken(), 10);
  const height = parseInt(readToken(), 10);
  readToken(); // maxval — 값이 이미 3종(0·205·254)으로 양자화돼 있어 쓰지 않는다
  pos++; // maxval 뒤 공백 1개, 그다음이 픽셀 시작
  const pixels = bytes.subarray(pos, pos + width * height);
  if (pixels.length < width * height) throw new Error("픽셀 데이터가 부족합니다");
  return { width, height, pixels };
}

/** 메타데이터 폴백용 yaml 파싱 — resolution 과 origin 만 쓴다. */
export function parseYaml(text: string): Meta | null {
  const res = /resolution:\s*([-0-9.eE]+)/.exec(text);
  const origin = /origin:\s*\[([^\]]+)]/.exec(text);
  if (!res || !origin) return null;
  const parts = origin[1].split(",").map(s => parseFloat(s.trim()));
  if (parts.length < 2 || parts.some(isNaN)) return null;
  return { resolution: parseFloat(res[1]), originX: parts[0], originY: parts[1] };
}

// 셀 값 → 색. 3종 외 값은 미지로 떨어뜨린다(mode 가 바뀌어도 안 깨지게).
//
// 색 상수는 palette.ts 로 옮겼다 (S15P11A301-227). 메인 화면의 실시간 지도가 같은
// 격자를 그리므로 두 곳에 복사하면 언젠가 한쪽만 바뀐다.

/**
 * 셀 값 → 색 (S15P11A301-223 에서 시험용으로 노출).
 *
 * **정확한 값 비교여야 한다.** nav2 의 점유도 계산은 (255 - v) / 255 인데
 * 미탐사 값 205 는 0.196 이고, map_saver 가 함께 저장하는 free_thresh 가 0.25 다.
 * 임계값으로 판정하면 205 가 free 로 떨어져 **미탐사 영역 전체가 탐사된 바닥으로
 * 그려진다.** 벽은 그대로 맞으므로 눈으로는 알 수 없다.
 */
export function cellColor(v: number): readonly number[] {
  return v === 0 ? COLOR_OCCUPIED : v === 254 ? COLOR_FREE : COLOR_UNKNOWN;
}

// 좌표 변환은 실시간 SLAM 지도와 공유한다(S15P11A301-227). 규칙이 갈라지면 두
// 화면의 로봇 위치가 서로 반대로 찍힌다. 기존 호출부와 시험이 여기서 가져가고
// 있어 다시 내보낸다.
export { worldToPixel } from "@/lib/gridGeometry";

const MAX_DISPLAY_HEIGHT = 420;
const MAX_ZOOM = 8;

export default function MissionMap({ missionId, encounters, onEncounterClick, fill = false }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<MapState>("loading");
  const [errorText, setErrorText] = useState("");
  const [pgm, setPgm] = useState<Pgm | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [trajectory, setTrajectory] = useState<TrajectoryPoint[]>([]);

  // 격자 오프스크린 캐시 — 휠·드래그마다 픽셀을 다시 만들지 않는다
  const offRef = useRef<HTMLCanvasElement | null>(null);
  // 뷰(확대·이동)는 리렌더 없이 ref 로 관리하고 draw() 를 직접 부른다
  const viewRef = useRef({ zoom: 1, panX: 0, panY: 0 });
  const baseScaleRef = useRef(1);
  const dragRef = useRef<{ startX: number; startY: number; panX: number; panY: number; moved: boolean } | null>(null);
  const markerHits = useRef<{ x: number; y: number; encounter: EncounterSummary }[]>([]);

  // 지도·궤적 로드
  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setPgm(null);
    setMeta(null);
    setTrajectory([]);
    viewRef.current = { zoom: 1, panX: 0, panY: 0 };

    (async () => {
      let view: MapView;
      try {
        view = await api.missionMap(missionId);
      } catch (e) {
        if (!cancelled) setState(e instanceof ApiError && e.httpStatus === 404 ? "no-map" : "error");
        if (e instanceof ApiError && e.httpStatus !== 404) setErrorText(e.message);
        return;
      }

      // 좌표계 메타: 전정밀 응답 우선, 없으면 yaml 폴백
      let m: Meta | null = null;
      if (view.resolution !== null && view.originX !== null && view.originY !== null) {
        m = { resolution: view.resolution, originX: view.originX, originY: view.originY };
      } else {
        try {
          const yamlRes = await fetch(view.yamlUrl);
          if (yamlRes.ok) m = parseYaml(await yamlRes.text());
        } catch { /* 아래에서 pgm 결과와 함께 판정 */ }
      }

      // pgm 다운로드 — 임무 중(파일 업로드 전)이면 404 가 정상 경로다
      try {
        const pgmRes = await fetch(view.pgmUrl);
        if (!pgmRes.ok) {
          if (!cancelled) setState("pending-upload");
          return;
        }
        const parsed = parsePgm(await pgmRes.arrayBuffer());
        if (cancelled) return;
        setPgm(parsed);
        setMeta(m);
        setState("ready");
      } catch (e) {
        if (!cancelled) {
          setErrorText(e instanceof Error ? e.message : "지도를 읽지 못했습니다");
          setState("error");
        }
      }

      // 궤적은 실패해도 지도 표시를 막지 않는다
      api.missionTrajectory(missionId, 1000)
        .then(t => { if (!cancelled) setTrajectory(t.points); })
        .catch(() => {});
    })();

    return () => { cancelled = true; };
  }, [missionId]);

  // pgm → 오프스크린 격자 (pgm 이 바뀔 때 1회)
  useEffect(() => {
    if (!pgm) { offRef.current = null; return; }
    const off = document.createElement("canvas");
    off.width = pgm.width;
    off.height = pgm.height;
    const ctx = off.getContext("2d")!;
    const img = ctx.createImageData(pgm.width, pgm.height);
    for (let i = 0; i < pgm.pixels.length; i++) {
      const v = pgm.pixels[i];
      const c = cellColor(v);
      img.data[i * 4] = c[0];
      img.data[i * 4 + 1] = c[1];
      img.data[i * 4 + 2] = c[2];
      img.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    offRef.current = off;
  }, [pgm]);

  /** 현재 뷰(확대·이동)로 전체를 다시 그린다. */
  const draw = useCallback(() => {
    if (!pgm || !offRef.current || !canvasRef.current || !containerRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // 기준 배율: 컨테이너 폭·높이에 맞춰 꽉 채운다. clientWidth/Height 는 p-3
    // 패딩을 포함하므로(border-box) 캔버스가 패딩을 침범하지 않게 빼고 잰다.
    const PADDING = 24;
    const containerW = containerRef.current.clientWidth - PADDING;
    const containerH = fill
      ? Math.max(containerRef.current.clientHeight - PADDING, 100)
      : MAX_DISPLAY_HEIGHT;
    const baseScale = Math.min(containerW / pgm.width, containerH / pgm.height);
    baseScaleRef.current = baseScale;
    const dispW = Math.round(pgm.width * baseScale);
    const dispH = Math.round(pgm.height * baseScale);
    if (canvas.width !== dispW || canvas.height !== dispH) {
      canvas.width = dispW;
      canvas.height = dispH;
    }

    const { zoom, panX, panY } = viewRef.current;
    const k = baseScale * zoom;

    ctx.fillStyle = `rgb(${COLOR_UNKNOWN.join(",")})`;
    ctx.fillRect(0, 0, dispW, dispH);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(offRef.current, panX, panY, pgm.width * k, pgm.height * k);

    markerHits.current = [];
    if (!meta) return; // 좌표계를 모르면 격자만 보여준다

    // map 좌표(미터) → 화면 픽셀. 확대·이동만 여기서 얹는다.
    const toScreen = (x: number, y: number) => {
      const { col, row } = worldToPixel(x, y, meta, pgm.height);
      return { x: col * k + panX, y: row * k + panY };
    };

    // 주행 궤적
    if (trajectory.length > 1) {
      ctx.strokeStyle = "#45c98c";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      trajectory.forEach((p, i) => {
        const s = toScreen(p.x, p.y);
        if (i === 0) ctx.moveTo(s.x, s.y);
        else ctx.lineTo(s.x, s.y);
      });
      ctx.stroke();
      const end = toScreen(trajectory[trajectory.length - 1].x, trajectory[trajectory.length - 1].y);
      ctx.fillStyle = "#45c98c";
      ctx.beginPath();
      ctx.arc(end.x, end.y, 4, 0, Math.PI * 2);
      ctx.fill();
    }

    // 발견 마커 — 좌표 있는 발견만. 마커 크기는 확대와 무관하게 화면 고정.
    encounters.filter(e => e.mapX !== null && e.mapY !== null).forEach(e => {
      const s = toScreen(e.mapX!, e.mapY!);
      ctx.fillStyle = "#e2a542";
      ctx.strokeStyle = "#12171f";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(s.x, s.y, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      markerHits.current.push({ x: s.x, y: s.y, encounter: e });
    });
  }, [pgm, meta, trajectory, encounters, fill]);

  useEffect(() => {
    if (state === "ready") draw();
  }, [state, draw]);

  // fill 모드는 컨테이너 크기가 창을 따라간다 — 창이 바뀌면 배율을 다시 잡는다.
  useEffect(() => {
    if (!fill || state !== "ready") return;
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [fill, state, draw]);

  // 이동 범위 제한 — 지도가 뷰포트를 완전히 벗어나지 않게
  const clampPan = useCallback((panX: number, panY: number, zoom: number) => {
    if (!pgm || !canvasRef.current) return { panX, panY };
    const k = baseScaleRef.current * zoom;
    const minX = Math.min(0, canvasRef.current.width - pgm.width * k);
    const minY = Math.min(0, canvasRef.current.height - pgm.height * k);
    return {
      panX: Math.max(minX, Math.min(0, panX)),
      panY: Math.max(minY, Math.min(0, panY)),
    };
  }, [pgm]);

  // 휠 확대 — 커서 위치 기준. 브라우저 페이지 스크롤을 막아야 해서 passive:false 로 직접 건다.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || state !== "ready") return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cx = ev.clientX - rect.left;
      const cy = ev.clientY - rect.top;
      const v = viewRef.current;
      const next = Math.max(1, Math.min(MAX_ZOOM, v.zoom * (ev.deltaY < 0 ? 1.2 : 1 / 1.2)));
      if (next === v.zoom) return;
      // 커서가 가리키던 지점이 그대로 있도록 이동값을 보정한다
      const ratio = next / v.zoom;
      const panned = clampPan(cx - (cx - v.panX) * ratio, cy - (cy - v.panY) * ratio, next);
      viewRef.current = next === 1 ? { zoom: 1, panX: 0, panY: 0 } : { zoom: next, ...panned };
      draw();
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [state, draw, clampPan]);

  const handleMouseDown = useCallback((ev: React.MouseEvent<HTMLCanvasElement>) => {
    const v = viewRef.current;
    dragRef.current = { startX: ev.clientX, startY: ev.clientY, panX: v.panX, panY: v.panY, moved: false };
  }, []);

  const handleMouseMove = useCallback((ev: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = ev.clientX - drag.startX;
    const dy = ev.clientY - drag.startY;
    if (Math.hypot(dx, dy) > 3) drag.moved = true;
    if (!drag.moved || viewRef.current.zoom === 1) return;
    const panned = clampPan(drag.panX + dx, drag.panY + dy, viewRef.current.zoom);
    viewRef.current = { ...viewRef.current, ...panned };
    draw();
  }, [draw, clampPan]);

  const handleMouseUp = useCallback((ev: React.MouseEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    dragRef.current = null;
    // 드래그가 아니었으면 클릭 — 마커 판정
    if (drag && !drag.moved) {
      const rect = ev.currentTarget.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      const hit = markerHits.current.find(h => Math.hypot(h.x - x, h.y - y) <= 10);
      if (hit) onEncounterClick(hit.encounter);
    }
  }, [onEncounterClick]);

  const handleDoubleClick = useCallback(() => {
    viewRef.current = { zoom: 1, panX: 0, panY: 0 };
    draw();
  }, [draw]);

  const markerCount = encounters.filter(e => e.mapX !== null && e.mapY !== null).length;

  return (
    <div className={`border border-border rounded bg-card ${fill ? "h-full flex flex-col overflow-hidden" : ""}`}>
      <div className="flex-shrink-0 flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-1.5">
          <MapIcon size={11} className="text-primary" />
          <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">임무 지도</span>
          {state === "ready" && (
            <span className="font-mono text-[9px] text-muted-foreground/60 ml-2">
              휠 확대 · 드래그 이동 · 더블클릭 초기화
            </span>
          )}
        </div>
        {state === "ready" && (
          <div className="flex items-center gap-3 font-mono text-[9px] text-muted-foreground">
            <span><span className="inline-block w-2 h-2 mr-1 align-middle" style={{ background: "rgb(226,232,240)" }} />벽</span>
            <span><span className="inline-block w-2 h-2 mr-1 align-middle" style={{ background: "rgb(42,53,66)" }} />탐사</span>
            <span><span className="inline-block w-2 h-0.5 mr-1 align-middle" style={{ background: "#45c98c" }} />경로</span>
            {markerCount > 0 && (
              <span><span className="inline-block w-2 h-2 mr-1 rounded-full align-middle" style={{ background: "#e2a542" }} />발견 {markerCount}</span>
            )}
          </div>
        )}
      </div>

      <div
        ref={containerRef}
        className={`p-3 flex justify-center ${fill ? "flex-1 min-h-0 items-center overflow-hidden" : ""}`}
      >
        {state === "loading" && (
          <p className="font-mono text-[10px] text-muted-foreground py-8">지도 불러오는 중…</p>
        )}
        {state === "no-map" && (
          <p className="font-mono text-[10px] text-muted-foreground py-8">이 임무에는 지도가 없습니다</p>
        )}
        {state === "pending-upload" && (
          <p className="font-mono text-[10px] text-muted-foreground py-8">
            지도 업로드 대기 중 — 임무가 끝나면 로봇이 지도를 올립니다
          </p>
        )}
        {state === "error" && (
          <p className="font-mono text-[10px] text-destructive py-8">{errorText || "지도를 읽지 못했습니다"}</p>
        )}
        {state === "ready" && (
          <canvas
            ref={canvasRef}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={() => { dragRef.current = null; }}
            onDoubleClick={handleDoubleClick}
            className="cursor-grab active:cursor-grabbing"
            style={{ imageRendering: "pixelated" }}
          />
        )}
      </div>

      {state === "ready" && !meta && (
        <p className="px-3 pb-2 font-mono text-[9px] text-accent">
          좌표 정보를 읽지 못해 발견 위치·경로는 표시하지 못합니다
        </p>
      )}
    </div>
  );
}

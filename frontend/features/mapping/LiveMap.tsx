"use client";

import { useEffect, useRef, useState } from "react";
import {
  startMapClient,
  type MapClientState,
} from "@/lib/foxgloveMapClient";
import { classifyGridCell, type OccupancyGrid } from "@/lib/occupancyGrid";
import { worldToPixel } from "@/lib/gridGeometry";
import {
  arrowRotationFromMapYaw,
  mapYawDegrees,
} from "@/lib/mapHeading";
import type { RobotPose } from "@/lib/robotPose";
import { OverlayLine, OverlayStack } from "@/features/telemetry/PanelOverlay";
import { COLOR_FREE, COLOR_OCCUPIED, COLOR_UNKNOWN } from "./palette";

/**
 * 실시간 SLAM 지도 (S15P11A301-227).
 *
 * 젯슨의 `foxglove_bridge`에 직접 붙어 `/map`, `/pose`, `/pose/fused`를 받는다.
 * 지도는 약 2초, 위치는 fused pose 기준 20Hz다. `/pose/fused`는 SLAM의 map→odom과
 * EKF(IMU)의 odom→base를 합친 값이며, 없거나 끊기면 기존 `/pose`로 돌아간다.
 * 백엔드를 거치지 않는다 —
 * telemetry 스키마에 격자 필드가 없고 지도 조회 API는 임무 종료 후 PGM뿐이라, 그
 * 둘로는 실시간이 안 된다.
 *
 * 임무 이력의 지도(S15P11A301-203)와 값 체계가 다르다. 그쪽은 PGM 바이트, 이쪽은
 * nav2 `int8`이다. 판정은 `classifyGridCell`이 하고 **색·좌표 변환·범례는 공유한다**
 * (`palette.ts`, `gridGeometry.ts`). 규칙이 갈라지면 두 화면의 로봇 위치가 서로
 * 반대로 찍히거나 같은 색이 다른 뜻이 된다.
 *
 * ## 큰 화면과 작은 화면
 *
 * 사이드바 148px 슬롯과 메인 영역이 같은 컴포넌트를 쓴다. 예전에는 메인이 목업
 * 격자(`LidarMap`)여서 **키우면 실시간 지도가 사라졌다.**
 *
 * 정보량은 크기에 맞춘다. 148px에 격자 크기·해상도까지 적으면 지도보다 글자가
 * 넓다. 좁을 때는 상태 한 낱말만 남긴다.
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

/**
 * 로봇 화살표 크기(화면 픽셀).
 *
 * 확대율과 무관하게 **화면 고정**이다. 격자 셀 기준으로 잡으면 0.05m/셀 지도에서
 * 로봇(0.3m 급)이 6픽셀짜리 점이 되어 방향을 알 수 없다. 임무 이력 지도의 발견
 * 마커도 같은 방식이다.
 */
const ARROW_LENGTH_PX = 13;
const ARROW_HALF_WIDTH_PX = 6;

/** 화살표 색. 임무 이력 지도의 궤적·끝점과 같은 초록이다. */
const COLOR_ROBOT = "#45c98c";
const COLOR_ROBOT_EDGE = "#12171f";

/** 위치를 못 받은 지 이 시간이 지나면 화살표를 흐리게 한다. */
const POSE_STALE_MS = 5_000;

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

/**
 * 로봇을 화살표로 그린다. 좌표는 이미 화면 픽셀이고 각도는 map 프레임의 yaw 다.
 *
 * **회전 부호는 `arrowRotationFromMapYaw` 가 소유한다** — 이 캔버스가 상하
 * 반전(`renderGrid` 의 `height-1-row`)이라 화면 회전이 map yaw 의 반대 부호이며,
 * 그 근거와 실측 이력이 `mapHeading.ts` 에 있다(S15P11A301-364). 여기서 부호를
 * 또 만지면 두 곳이 싸운다. 센서·EKF 쪽 부호는 어느 경우에도 바꾸지 않는다.
 */
function drawRobot(
  ctx: CanvasRenderingContext2D,
  screenX: number,
  screenY: number,
  yaw: number,
  fresh: boolean,
) {
  ctx.save();
  ctx.translate(screenX, screenY);
  ctx.rotate(arrowRotationFromMapYaw(yaw));
  ctx.globalAlpha = fresh ? 1 : 0.35;

  ctx.beginPath();
  ctx.moveTo(ARROW_LENGTH_PX, 0);
  ctx.lineTo(-ARROW_LENGTH_PX * 0.5, -ARROW_HALF_WIDTH_PX);
  ctx.lineTo(-ARROW_LENGTH_PX * 0.2, 0);
  ctx.lineTo(-ARROW_LENGTH_PX * 0.5, ARROW_HALF_WIDTH_PX);
  ctx.closePath();

  ctx.fillStyle = COLOR_ROBOT;
  ctx.fill();
  // 어두운 미탐색 영역 위에서도 형태가 보이게 테두리를 둔다.
  ctx.strokeStyle = COLOR_ROBOT_EDGE;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
}

interface Meta {
  w: number;
  h: number;
  res: number;
}

interface PoseText {
  coords: string;
  bearing: string;
}

/** `full` 은 메인 영역, `compact` 는 사이드바 슬롯이다. 둘 다 16:9 상자이며
 *  높이는 폭에서 유도된다(S15P11A301-259, `page.tsx` 의 `SLOT_CLASS`). */
export default function LiveMap({
  variant = "compact",
}: { variant?: "compact" | "full" } = {}) {
  const full = variant === "full";
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const gridRef = useRef<OccupancyGrid | null>(null);
  const poseRef = useRef<{ pose: RobotPose; at: number } | null>(null);
  const [state, setState] = useState<MapClientState>("connecting");
  const [detail, setDetail] = useState<string | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  // 좌표를 상태로 두는 것은 글자 표시용이다. 그리기는 ref 로 한다 — 4~5Hz 로
  // 리렌더하면 캔버스가 매번 다시 만들어진다.
  const [poseText, setPoseText] = useState<PoseText | null>(null);

  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_MAP_WS_URL || DEFAULT_URL;
    return startMapClient(url, {
      onGrid: grid => {
        gridRef.current = grid;
        setMeta({ w: grid.width, h: grid.height, res: grid.resolution });
        draw();
      },
      onPose: pose => {
        // frame_id 가 map 이 아니면 지도와 다른 좌표계다. 그대로 찍으면 로봇이
        // 엉뚱한 곳에 그려지므로 버린다.
        if (pose.frameId !== "map") return;
        poseRef.current = { pose, at: Date.now() };
        setPoseText({
          coords: `${pose.x.toFixed(2)}, ${pose.y.toFixed(2)} m`,
          bearing: `${mapYawDegrees(pose.yaw).toFixed(1)}°`,
        });
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
    const offsetX = (box.width - drawW) / 2;
    const offsetY = (box.height - drawH) / 2;
    ctx.drawImage(offscreen, offsetX, offsetY, drawW, drawH);

    const tracked = poseRef.current;
    if (!tracked) return;
    // 격자 픽셀 → 화면 픽셀. drawImage 에 쓴 것과 **같은** scale·offset 이어야
    // 한다. 따로 계산하면 창 크기에 따라 화살표만 어긋난다.
    const { col, row } = worldToPixel(
      tracked.pose.x,
      tracked.pose.y,
      grid,
      grid.height,
    );
    drawRobot(
      ctx,
      offsetX + col * scale,
      offsetY + row * scale,
      tracked.pose.yaw,
      Date.now() - tracked.at < POSE_STALE_MS,
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

      <OverlayStack>
        <OverlayLine
          kind="SLAM"
          tone={STATE_TONE[state]}
          title={
            detail ??
            "젯슨의 foxglove_bridge 에서 /map 과 /pose 를 직접 받는다. 같은 네트워크에서만 보인다."
          }
        >
          {/* 격자 크기·해상도는 큰 화면에서만 적는다. 148px 슬롯에서는 지도보다
              글자가 넓어져 정작 지도가 안 보인다. */}
          {full && meta
            ? `${STATE_LABEL[state]} · ${meta.w}×${meta.h} · ${meta.res.toFixed(2)}m/셀`
            : STATE_LABEL[state]}
        </OverlayLine>
      </OverlayStack>

      {full && <Legend />}
      {full && hasGrid && <PoseReadout pose={poseText} />}
    </div>
  );
}

/**
 * 범례. **임무 이력의 임무 지도와 같은 낱말·같은 색·같은 형식이다.**
 *
 * 두 화면이 같은 격자를 보여주는데 한쪽은 "장애물·이동가능·미탐색", 다른 쪽은
 * "벽·탐사"라고 적으면 다른 것을 보고 있다고 읽힌다. 색을 맞춰 놓고 이름을
 * 다르게 두는 것이 더 나쁘다.
 *
 * 미탐색은 넣지 않는다 — 패널 배경과 같은 색이라 범례에 두면 빈 칸으로 보인다.
 * 임무 지도도 같은 이유로 빼 두었다.
 */
function Legend() {
  return (
    <div className="absolute top-1.5 right-1.5 z-10 flex items-center gap-3 rounded bg-black/50 px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground">
      <span>
        <span
          className="mr-1 inline-block h-2 w-2 align-middle"
          style={{ background: `rgb(${COLOR_OCCUPIED.join(",")})` }}
        />
        벽
      </span>
      <span>
        <span
          className="mr-1 inline-block h-2 w-2 align-middle"
          style={{ background: `rgb(${COLOR_FREE.join(",")})` }}
        />
        탐사
      </span>
      <span>
        <span
          className="mr-1 inline-block h-2 w-2 rounded-full align-middle"
          style={{ background: COLOR_ROBOT }}
        />
        로봇
      </span>
    </div>
  );
}

/**
 * 좌표 표시. 큰 화면에서만 나온다.
 *
 * **미터 단위 map 좌표다.** 예전 목업은 격자 행·열(`행:10.0 열:10.0`)을 적었는데
 * 그건 화면 내부 인덱스라 현장에서 쓸 수 없는 값이었다.
 *
 * **나침반을 두지 않는다.** 방위는 map 프레임 기준이고, map 프레임의 x축은 SLAM을
 * 시작한 순간 로봇이 향한 방향이다. 자북과는 아무 관계가 없다(자력계가 없다).
 * 예전 목업의 N·E·S·W 나침반은 없는 근거를 있는 것처럼 보이게 했다.
 */
function PoseReadout({ pose }: { pose: PoseText | null }) {
  return (
    <div className="absolute bottom-2 left-2 z-10 space-y-0.5 rounded bg-black/50 px-1.5 py-1 font-mono text-[10px] text-muted-foreground">
      {pose ? (
        <>
          <div>좌표 {pose.coords}</div>
          <div title="map 프레임 기준. 자북이 아니다 — 시작 시 로봇이 향한 방향이 0°다.">
            방위 {pose.bearing}
          </div>
        </>
      ) : (
        <div>위치 대기</div>
      )}
    </div>
  );
}

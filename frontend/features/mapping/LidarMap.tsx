"use client";

import { useEffect, useRef, useState } from "react";
import { useRobot, type RobotPos } from "@/features/robot/RobotContext";
import { GRID_SIZE, type DetectionEvent } from "@/features/robot/mockData";

const CELL_PX = 6;

function drawGrid(
  ctx: CanvasRenderingContext2D,
  grid: number[][],
  robotPos: RobotPos,
  pathHistory: RobotPos[],
  detections: DetectionEvent[],
  scale: number,
  offsetX: number,
  offsetY: number
) {
  const sz = CELL_PX * scale;
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#12171f";
  ctx.fillRect(0, 0, W, H);

  for (let r = 0; r < GRID_SIZE; r++) {
    for (let c = 0; c < GRID_SIZE; c++) {
      const x = c * sz + offsetX;
      const y = r * sz + offsetY;
      if (x + sz < 0 || x > W || y + sz < 0 || y > H) continue;

      const val = grid[r][c];
      if (val === -1) {
        ctx.fillStyle = "#1b222c"; // 미탐색
      } else if (val === 0) {
        ctx.fillStyle = "#242e3b"; // 이동 가능
      } else {
        ctx.fillStyle = "#d7e1ec"; // 장애물 — SLAM 지도 관례대로 밝게
      }
      ctx.fillRect(x, y, sz - 0.5, sz - 0.5);
    }
  }

  ctx.strokeStyle = "rgba(255,255,255,0.04)";
  ctx.lineWidth = 0.5;
  for (let r = 0; r <= GRID_SIZE; r += 5) {
    const y = r * sz + offsetY;
    ctx.beginPath();
    ctx.moveTo(offsetX, y);
    ctx.lineTo(offsetX + GRID_SIZE * sz, y);
    ctx.stroke();
  }

  if (pathHistory.length > 1) {
    ctx.beginPath();
    ctx.strokeStyle = "rgba(69,201,140,0.5)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([3, 3]);
    pathHistory.forEach((p, i) => {
      const x = p.c * sz + offsetX + sz / 2;
      const y = p.r * sz + offsetY + sz / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  detections.forEach(det => {
    const x = det.gridC * sz + offsetX + sz / 2;
    const y = det.gridR * sz + offsetY + sz / 2;
    const radius = 3 * sz;

    const grad = ctx.createRadialGradient(x, y, 0, x, y, radius);
    grad.addColorStop(0, "rgba(226,165,66,0.35)");
    grad.addColorStop(1, "rgba(226,165,66,0)");
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    ctx.arc(x, y, sz * 0.8, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(226,165,66,0.95)";
    ctx.fill();

    ctx.fillStyle = "#fff";
    ctx.font = `bold ${sz * 1.2}px JetBrains Mono`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("!", x, y);
  });

  const rx = robotPos.c * sz + offsetX + sz / 2;
  const ry = robotPos.r * sz + offsetY + sz / 2;
  const headingRad = (robotPos.heading * Math.PI) / 180;
  const iconSize = sz * 2.5;

  const glow = ctx.createRadialGradient(rx, ry, 0, rx, ry, iconSize * 2);
  glow.addColorStop(0, "rgba(69,201,140,0.22)");
  glow.addColorStop(1, "rgba(69,201,140,0)");
  ctx.beginPath();
  ctx.arc(rx, ry, iconSize * 2, 0, Math.PI * 2);
  ctx.fillStyle = glow;
  ctx.fill();

  ctx.save();
  ctx.translate(rx, ry);
  ctx.rotate(headingRad);
  ctx.beginPath();
  ctx.moveTo(0, -iconSize);
  ctx.lineTo(-iconSize * 0.6, iconSize * 0.7);
  ctx.lineTo(0, iconSize * 0.3);
  ctx.lineTo(iconSize * 0.6, iconSize * 0.7);
  ctx.closePath();
  ctx.fillStyle = "#45c98c";
  ctx.fill();
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();
}

/**
 * @param compact 사이드바 180px 슬롯용. 제목·범례·나침반·좌표는 전체 화면
 * 크기를 전제로 모서리에 절대배치돼 있어서, 축소하면 서로 겹쳐 찍히고 지도를
 * 덮는다. 축소 시에는 캔버스만 남긴다.
 */
export default function LidarMap({ compact = false }: { compact?: boolean } = {}) {
  const { grid, robotPos, pathHistory, detections } = useRobot();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1.0);
  const [offset, setOffset] = useState({ x: 20, y: 20 });
  const dragging = useRef<{ startX: number; startY: number; ox: number; oy: number } | null>(null);
  const animFrame = useRef(0);
  const followRobot = useRef(true);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const resize = () => {
      canvas.width = container.clientWidth;
      canvas.height = container.clientHeight;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    cancelAnimationFrame(animFrame.current);
    animFrame.current = requestAnimationFrame(() => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      let ox = offset.x;
      let oy = offset.y;
      if (followRobot.current) {
        const sz = CELL_PX * scale;
        ox = canvas.width / 2 - robotPos.c * sz;
        oy = canvas.height / 2 - robotPos.r * sz;
      }
      drawGrid(ctx, grid, robotPos, pathHistory, detections, scale, ox, oy);
    });
  }, [grid, robotPos, pathHistory, detections, scale, offset]);

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    followRobot.current = false;
    setScale(s => Math.max(0.5, Math.min(3, s - e.deltaY * 0.001)));
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    followRobot.current = false;
    dragging.current = { startX: e.clientX, startY: e.clientY, ox: offset.x, oy: offset.y };
  };
  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragging.current) return;
    setOffset({
      x: dragging.current.ox + e.clientX - dragging.current.startX,
      y: dragging.current.oy + e.clientY - dragging.current.startY,
    });
  };
  const handleMouseUp = () => { dragging.current = null; };

  return (
    <div ref={containerRef} className="relative size-full bg-[#12171f] overflow-hidden select-none">
      <canvas
        ref={canvasRef}
        className="absolute inset-0 cursor-grab active:cursor-grabbing"
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      />

      {!compact && (
      <>
      <div className="absolute top-3 left-3 flex items-center gap-2">
        <span className="font-mono text-[10px] text-primary/60 tracking-widest uppercase">LiDAR 그리드</span>
        <span className="font-mono text-[10px] text-muted-foreground">{GRID_SIZE}×{GRID_SIZE}</span>
      </div>

      <div className="absolute top-3 left-1/2 -translate-x-1/2 font-mono text-[10px] text-primary/50 tracking-widest uppercase">
        점유 지도 — 실시간
      </div>

      <div className="absolute bottom-3 left-3 font-mono text-[10px] text-muted-foreground space-y-0.5">
        <div>행:{robotPos.r.toFixed(1)} 열:{robotPos.c.toFixed(1)}</div>
        <div>방위:{Math.round(robotPos.heading)}°</div>
        <div>배율:{scale.toFixed(2)}×</div>
      </div>

      <div className="absolute bottom-3 right-3 w-10 h-10">
        <svg viewBox="0 0 40 40" className="w-full h-full">
          <circle cx="20" cy="20" r="19" stroke="rgba(255,255,255,0.14)" strokeWidth="1" fill="none" />
          {["N","E","S","W"].map((dir, i) => {
            const angle = i * 90;
            const rad = (angle - 90) * Math.PI / 180;
            return (
              <text key={dir} x={20 + 14 * Math.cos(rad)} y={20 + 14 * Math.sin(rad) + 3}
                fontSize="7" fill={dir === "N" ? "#45c98c" : "#5e6c7d"}
                textAnchor="middle" fontFamily="JetBrains Mono">
                {dir}
              </text>
            );
          })}
          <polygon
            points="20,8 23,20 20,17 17,20"
            fill="#45c98c"
            transform={`rotate(${robotPos.heading}, 20, 20)`}
          />
        </svg>
      </div>

      <div className="absolute top-3 right-3 space-y-1">
        {[
          { color: "#d7e1ec", label: "장애물" },
          { color: "#242e3b", label: "이동가능" },
          { color: "#1b222c", label: "미탐색" },
          { color: "#e2a542", label: "탐지" },
        ].map(({ color, label }) => (
          <div key={label} className="flex items-center gap-1.5">
            <div className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: color }} />
            <span className="font-mono text-[9px] text-muted-foreground">{label}</span>
          </div>
        ))}
      </div>

      <button
        onClick={() => { followRobot.current = !followRobot.current; }}
        className="absolute bottom-3 left-1/2 -translate-x-1/2 font-mono text-[11px] px-2 py-0.5 border border-border rounded text-muted-foreground hover:text-primary hover:border-primary/30 transition-colors"
      >
        {followRobot.current ? "추적 중" : "자유 시점"}
      </button>
      </>
      )}
    </div>
  );
}

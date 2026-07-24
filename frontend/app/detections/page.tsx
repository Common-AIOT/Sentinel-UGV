"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { ChevronLeft, User, Filter, MapPin, Clock, TrendingUp } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import type { DetectionEvent } from "@/features/robot/mockData";
import { GRID_SIZE } from "@/features/robot/mockData";

type SortKey = "time" | "confidence";
type FilterLevel = "all" | "high" | "medium" | "low";

function confidenceLabel(v: number) {
  if (v >= 0.9) return { label: "높음", cls: "text-destructive border-destructive/30 bg-destructive/10" };
  if (v >= 0.75) return { label: "중간", cls: "text-accent border-accent/30 bg-accent/10" };
  return { label: "낮음", cls: "text-primary border-primary/30 bg-primary/10" };
}

function MiniMap({ detections, selected }: { detections: DetectionEvent[]; selected: DetectionEvent | null }) {
  const cellPx = 4;
  const size = GRID_SIZE * cellPx;

  return (
    <div className="relative border border-border rounded overflow-hidden bg-[#12171f]" style={{ width: size, height: size }}>
      <svg className="absolute inset-0" width={size} height={size}>
        {Array.from({ length: Math.floor(GRID_SIZE / 10) + 1 }).map((_, i) => (
          <g key={i}>
            <line x1={i * 10 * cellPx} y1={0} x2={i * 10 * cellPx} y2={size} stroke="rgba(255,255,255,0.05)" strokeWidth="0.5" />
            <line x1={0} y1={i * 10 * cellPx} x2={size} y2={i * 10 * cellPx} stroke="rgba(255,255,255,0.05)" strokeWidth="0.5" />
          </g>
        ))}
        {detections.map(det => {
          const x = det.gridC * cellPx;
          const y = det.gridR * cellPx;
          const isSelected = selected?.id === det.id;
          const { label } = confidenceLabel(det.confidence);
          const color = label === "높음" ? "#e5534b" : label === "중간" ? "#e2a542" : "#45c98c";
          return (
            <g key={det.id}>
              {isSelected && <circle cx={x} cy={y} r={10} fill={color} fillOpacity={0.15} />}
              <circle cx={x} cy={y} r={isSelected ? 4 : 2.5} fill={color} fillOpacity={isSelected ? 1 : 0.8} />
            </g>
          );
        })}
      </svg>
      <div className="absolute top-1 left-1">
        <span className="font-mono text-[8px] text-primary/40">점유 지도</span>
      </div>
      <div className="absolute bottom-1 right-1 font-mono text-[8px] text-muted-foreground/40">
        {detections.length}개 마커
      </div>
    </div>
  );
}

function DetectionRow({ det, selected, onClick }: { det: DetectionEvent; selected: boolean; onClick: () => void }) {
  const { label, cls } = confidenceLabel(det.confidence);
  return (
    <button
      onClick={onClick}
      className={`w-full text-left flex items-center gap-3 px-3 py-2.5 border-b border-border transition-colors ${
        selected ? "bg-primary/5 border-l-2 border-l-primary" : "hover:bg-secondary/30"
      }`}
    >
      <div className="w-10 h-10 rounded flex-shrink-0 flex items-center justify-center" style={{ backgroundColor: det.thumbnailColor }}>
        <User size={16} className="text-white/60" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`font-mono text-[9px] px-1 py-0.5 rounded border ${cls}`}>{label}</span>
          <span className="font-mono text-[10px] text-foreground font-medium">{Math.round(det.confidence * 100)}%</span>
        </div>
        <p className="font-mono text-[10px] text-muted-foreground truncate">{det.location}</p>
      </div>
      <div className="flex-shrink-0 text-right space-y-0.5">
        <p className="font-mono text-[9px] text-muted-foreground">{new Date(det.timestamp).toLocaleTimeString()}</p>
        <p className="font-mono text-[8px] text-muted-foreground/50">행:{det.gridR} 열:{det.gridC}</p>
      </div>
    </button>
  );
}

export default function DetectionPage() {
  const { detections } = useRobot();
  const [selected, setSelected] = useState<DetectionEvent | null>(null);
  const [sort, setSort] = useState<SortKey>("time");
  const [filter, setFilter] = useState<FilterLevel>("all");

  const filtered = useMemo(() => {
    let list = [...detections];
    if (filter === "high") list = list.filter(d => d.confidence >= 0.9);
    else if (filter === "medium") list = list.filter(d => d.confidence >= 0.75 && d.confidence < 0.9);
    else if (filter === "low") list = list.filter(d => d.confidence < 0.75);
    if (sort === "confidence") list.sort((a, b) => b.confidence - a.confidence);
    return list;
  }, [detections, sort, filter]);

  const stats = useMemo(() => ({
    total: detections.length,
    high: detections.filter(d => d.confidence >= 0.9).length,
    med: detections.filter(d => d.confidence >= 0.75 && d.confidence < 0.9).length,
    low: detections.filter(d => d.confidence < 0.75).length,
  }), [detections]);

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <div className="h-9 flex-shrink-0 flex items-center justify-between px-4 border-b border-border bg-card/60">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground hover:text-primary transition-colors">
            <ChevronLeft size={12} /> GCS
          </Link>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-1.5">
            <User size={11} className="text-accent" />
            <span className="font-mono text-[11px] text-accent">인명 탐지</span>
          </div>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">
          전체: <span className="text-foreground">{stats.total}</span>
        </span>
      </div>

      <div className="flex-1 flex overflow-hidden">
        <div className="flex flex-col border-r border-border" style={{ width: 340 }}>
          <div className="flex border-b border-border flex-shrink-0">
            {[
              { label: "높음", val: stats.high, color: "text-destructive" },
              { label: "중간", val: stats.med, color: "text-accent" },
              { label: "낮음", val: stats.low, color: "text-primary" },
            ].map(s => (
              <div key={s.label} className="flex-1 py-2 text-center border-r border-border last:border-r-0">
                <p className={`font-mono text-base font-semibold ${s.color}`}>{s.val}</p>
                <p className="font-mono text-[9px] text-muted-foreground">{s.label}</p>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-shrink-0">
            <Filter size={10} className="text-muted-foreground flex-shrink-0" />
            <select
              value={filter}
              onChange={e => setFilter(e.target.value as FilterLevel)}
              className="flex-1 font-mono text-[10px] bg-secondary/50 border border-border rounded px-2 py-1 text-foreground"
            >
              <option value="all">전체</option>
              <option value="high">높음 ≥90%</option>
              <option value="medium">중간 75~89%</option>
              <option value="low">낮음 &lt;75%</option>
            </select>
            <select
              value={sort}
              onChange={e => setSort(e.target.value as SortKey)}
              className="font-mono text-[10px] bg-secondary/50 border border-border rounded px-2 py-1 text-foreground"
            >
              <option value="time">최신순</option>
              <option value="confidence">신뢰도순</option>
            </select>
          </div>

          <div className="flex-1 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-2">
                <User size={24} className="text-muted-foreground/30" />
                <p className="font-mono text-[11px] text-muted-foreground">탐지 없음</p>
              </div>
            ) : (
              filtered.map(det => (
                <DetectionRow key={det.id} det={det} selected={selected?.id === det.id} onClick={() => setSelected(det === selected ? null : det)} />
              ))
            )}
          </div>
        </div>

        <div className="flex-1 flex flex-col overflow-hidden">
          {selected ? (
            <>
              <div className="border-b border-border p-5 flex gap-5 flex-shrink-0">
                <div
                  className="w-32 h-24 rounded flex items-center justify-center flex-shrink-0 border border-border"
                  style={{ backgroundColor: selected.thumbnailColor }}
                >
                  <User size={36} className="text-white/50" />
                </div>
                <div className="flex-1 space-y-3">
                  <div className="flex items-center gap-2">
                    <span className={`font-mono text-[10px] px-2 py-0.5 rounded border ${confidenceLabel(selected.confidence).cls}`}>
                      {confidenceLabel(selected.confidence).label} 신뢰도
                    </span>
                    <span className="font-mono text-lg font-semibold text-foreground">{Math.round(selected.confidence * 100)}%</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      [<Clock key="time" size={10} />, "시각", new Date(selected.timestamp).toLocaleString()],
                      [<MapPin key="loc" size={10} />, "위치", selected.location],
                      [<TrendingUp key="grid" size={10} />, "그리드 좌표", `행 ${selected.gridR}, 열 ${selected.gridC}`],
                      [null, "이벤트 ID", selected.id],
                    ].map(([icon, k, v], i) => (
                      <div key={i} className="border border-border rounded px-2 py-1.5 bg-secondary/20">
                        <div className="flex items-center gap-1 mb-0.5 text-muted-foreground">
                          {icon}
                          <span className="font-mono text-[9px] uppercase">{k as string}</span>
                        </div>
                        <p className="font-mono text-[10px] text-foreground">{v as string}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <div className="flex-1 flex items-center justify-center bg-background/50 p-6">
                <div className="space-y-3">
                  <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider text-center">
                    탐지 지도 — {filtered.length}개 이벤트
                  </p>
                  <MiniMap detections={filtered} selected={selected} />
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-4 bg-background/50">
              <div className="space-y-3 text-center">
                <p className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
                  탐지 지도 — {filtered.length}개 이벤트
                </p>
                <MiniMap detections={filtered} selected={null} />
                <p className="font-mono text-[10px] text-muted-foreground">목록에서 이벤트를 선택하면 상세 정보가 표시됩니다</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

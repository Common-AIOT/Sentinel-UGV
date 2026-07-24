"use client";

import { useState } from "react";
import { X, User, ChevronRight } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";
import type { DetectionEvent } from "@/features/robot/mockData";

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 90 ? "text-destructive border-destructive/30 bg-destructive/10"
    : pct >= 75 ? "text-accent border-accent/30 bg-accent/10"
    : "text-primary border-primary/30 bg-primary/10";
  return (
    <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border ${color}`}>
      {pct}%
    </span>
  );
}

function ThumbnailCard({ det, onClick }: { det: DetectionEvent; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex-shrink-0 group relative border border-border hover:border-accent/40 rounded overflow-hidden transition-colors bg-card"
      style={{ width: 72, height: 56 }}
    >
      <div className="w-full h-full flex items-center justify-center" style={{ backgroundColor: det.thumbnailColor }}>
        <User size={16} className="text-white/50" />
      </div>
      <div className="absolute inset-0 bg-gradient-to-t from-black/80 to-transparent" />
      <div className="absolute bottom-0.5 left-0.5 right-0.5">
        <ConfidenceBadge value={det.confidence} />
      </div>
      <ChevronRight size={10} className="absolute top-1 right-1 text-white/40 group-hover:text-accent transition-colors" />
    </button>
  );
}

export default function DetectionPanel() {
  const { detections, activeDetection, dismissDetection } = useRobot();
  const [selectedDet, setSelectedDet] = useState<DetectionEvent | null>(null);

  return (
    <>
      {/* Active detection popup */}
      {activeDetection && (
        <div className="absolute bottom-4 left-4 z-20 max-w-xs border border-accent/40 bg-card/95 backdrop-blur rounded p-3 shadow-xl animate-in slide-in-from-bottom-2">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
              <span className="font-mono text-[10px] text-accent uppercase tracking-widest">Human Detected</span>
            </div>
            <button onClick={dismissDetection} className="text-muted-foreground hover:text-foreground">
              <X size={12} />
            </button>
          </div>
          <div className="flex items-center gap-3">
            <div
              className="w-12 h-12 rounded flex items-center justify-center flex-shrink-0"
              style={{ backgroundColor: activeDetection.thumbnailColor }}
            >
              <User size={20} className="text-white/70" />
            </div>
            <div className="space-y-0.5">
              <p className="font-mono text-[10px] text-foreground">{activeDetection.location}</p>
              <p className="font-mono text-[9px] text-muted-foreground">
                {new Date(activeDetection.timestamp).toLocaleTimeString()}
              </p>
              <ConfidenceBadge value={activeDetection.confidence} />
            </div>
          </div>
        </div>
      )}

      {/* Thumbnail strip */}
      {detections.length > 0 && (
        <div className="flex flex-col gap-1.5 h-full overflow-y-auto pr-0.5">
          <span className="font-mono text-[9px] text-muted-foreground uppercase tracking-wider flex-shrink-0">
            Detections ({detections.length})
          </span>
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {detections.map(det => (
              <ThumbnailCard key={det.id} det={det} onClick={() => setSelectedDet(det)} />
            ))}
          </div>
        </div>
      )}

      {/* Detail modal */}
      {selectedDet && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center"
          onClick={() => setSelectedDet(null)}
        >
          <div
            className="bg-card border border-border rounded p-5 max-w-sm w-full mx-4 space-y-4"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-accent uppercase tracking-widest">Detection Detail</span>
              <button onClick={() => setSelectedDet(null)}>
                <X size={14} className="text-muted-foreground hover:text-foreground" />
              </button>
            </div>
            <div
              className="w-full h-32 rounded flex items-center justify-center"
              style={{ backgroundColor: selectedDet.thumbnailColor }}
            >
              <User size={40} className="text-white/50" />
            </div>
            <div className="space-y-2">
              {[
                ["Time", new Date(selectedDet.timestamp).toLocaleString()],
                ["Location", selectedDet.location],
                ["Confidence", `${Math.round(selectedDet.confidence * 100)}%`],
                ["Grid Pos", `R:${selectedDet.gridR} C:${selectedDet.gridC}`],
                ["Event ID", selectedDet.id],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span className="font-mono text-[10px] text-muted-foreground">{k}</span>
                  <span className="font-mono text-[10px] text-foreground">{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

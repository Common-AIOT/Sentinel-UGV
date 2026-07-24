"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { ChevronLeft, Play, Calendar, Film, User } from "lucide-react";
import { MOCK_BLACKBOX_ENTRIES } from "@/features/robot/mockData";

type Entry = typeof MOCK_BLACKBOX_ENTRIES[number];

function formatDuration(minutes: number) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}시간 ${m}분` : `${m}분`;
}

export default function BlackboxPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [playing, setPlaying] = useState<Entry | null>(null);
  const [showDetections, setShowDetections] = useState(true);
  const [progress, setProgress] = useState(0);

  const dates = useMemo(() => {
    const s = new Set(MOCK_BLACKBOX_ENTRIES.map(e => e.date));
    return [...s].sort().reverse();
  }, []);

  const filtered = useMemo(() =>
    selectedDate ? MOCK_BLACKBOX_ENTRIES.filter(e => e.date === selectedDate) : MOCK_BLACKBOX_ENTRIES,
    [selectedDate]
  );

  const handlePlay = (entry: Entry) => {
    setPlaying(entry);
    setProgress(0);
    const interval = setInterval(() => {
      setProgress(p => {
        if (p >= 100) { clearInterval(interval); return 100; }
        return p + 0.5;
      });
    }, 200);
  };

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <div className="h-9 flex-shrink-0 flex items-center justify-between px-4 border-b border-border bg-card/60">
        <div className="flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground hover:text-primary transition-colors">
            <ChevronLeft size={12} /> GCS
          </Link>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-1.5">
            <Film size={11} className="text-primary" />
            <span className="font-mono text-[11px] text-primary">블랙박스</span>
          </div>
        </div>
        <span className="font-mono text-[10px] text-muted-foreground">{MOCK_BLACKBOX_ENTRIES.length}개 녹화</span>
      </div>

      <div className="flex-1 flex overflow-hidden">
        <div className="w-48 flex-shrink-0 border-r border-border bg-card flex flex-col">
          <div className="p-3 border-b border-border">
            <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">날짜 필터</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            <button
              onClick={() => setSelectedDate(null)}
              className={`w-full text-left font-mono text-[10px] px-2 py-1.5 rounded transition-colors flex items-center gap-1.5 ${
                !selectedDate ? "bg-primary/10 text-primary border border-primary/20" : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
              }`}
            >
              <Calendar size={10} />
              전체 날짜
            </button>
            {dates.map(date => {
              const count = MOCK_BLACKBOX_ENTRIES.filter(e => e.date === date).length;
              return (
                <button
                  key={date}
                  onClick={() => setSelectedDate(date)}
                  className={`w-full text-left font-mono text-[10px] px-2 py-1.5 rounded transition-colors ${
                    selectedDate === date
                      ? "bg-primary/10 text-primary border border-primary/20"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                  }`}
                >
                  <div className="flex justify-between">
                    <span>{date}</span>
                    <span className="text-muted-foreground/60">{count}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex-1 flex flex-col overflow-hidden">
          {playing && (
            <div className="flex-shrink-0 border-b border-border bg-[#141a22] p-4">
              <div className="flex gap-4">
                <div
                  className="flex-shrink-0 rounded border border-border flex items-center justify-center relative overflow-hidden"
                  style={{ width: 320, height: 180, backgroundColor: `hsl(${playing.thumbnailHue}, 30%, 8%)` }}
                >
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-2">
                    <Play size={24} className="text-primary/40" />
                    <span className="font-mono text-[10px] text-primary/40">재생 시뮬레이션</span>
                  </div>
                  <div className="absolute bottom-0 left-0 h-0.5 bg-primary transition-all duration-200" style={{ width: `${progress}%` }} />
                  <div className="absolute left-0 right-0 h-0.5 bg-primary/20 pointer-events-none" style={{ top: `${progress}%` }} />
                  {showDetections && playing.detections > 0 && (
                    <div className="absolute bottom-2 left-2 right-2 flex gap-1">
                      {Array.from({ length: playing.detections }).map((_, i) => (
                        <div key={i} className="h-1 flex-1 bg-accent/70 rounded-full" style={{ opacity: 0.6 + i * 0.05 }} />
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex-1 space-y-3">
                  <div>
                    <p className="font-mono text-xs text-foreground">{playing.date}</p>
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {new Date(playing.startTime).toLocaleTimeString()} — {formatDuration(playing.duration)}
                    </p>
                  </div>
                  <div className="w-full bg-muted rounded-full h-1.5">
                    <div className="bg-primary h-full rounded-full transition-all" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <button onClick={() => setProgress(0)} className="font-mono text-[10px] px-2 py-1 border border-border rounded text-muted-foreground hover:text-foreground transition-colors">
                      ⏮ 처음으로
                    </button>
                    <button onClick={() => handlePlay(playing)} className="font-mono text-[10px] px-2 py-1 border border-primary/30 bg-primary/10 rounded text-primary hover:bg-primary/20 transition-colors">
                      ▶ 다시 재생
                    </button>
                    <button onClick={() => setPlaying(null)} className="font-mono text-[10px] px-2 py-1 border border-border rounded text-muted-foreground hover:text-foreground transition-colors">
                      ✕ 닫기
                    </button>
                    <label className="flex items-center gap-1.5 cursor-pointer ml-auto">
                      <input type="checkbox" checked={showDetections} onChange={e => setShowDetections(e.target.checked)} className="accent-primary w-3 h-3" />
                      <span className="font-mono text-[10px] text-muted-foreground">탐지 마커</span>
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      ["재생 시간", formatDuration(playing.duration)],
                      ["탐지 수", playing.detections.toString()],
                      ["파일 크기", playing.size],
                    ].map(([k, v]) => (
                      <div key={k} className="border border-border rounded px-2 py-1.5 bg-secondary/20">
                        <p className="font-mono text-[9px] text-muted-foreground">{k}</p>
                        <p className="font-mono text-xs text-foreground">{v}</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-4">
            <div className="space-y-2">
              {filtered.map(entry => (
                <div
                  key={entry.id}
                  className={`border rounded p-3 bg-card hover:border-primary/30 transition-colors cursor-pointer ${
                    playing?.id === entry.id ? "border-primary/40 bg-primary/5" : "border-border"
                  }`}
                  onClick={() => handlePlay(entry)}
                >
                  <div className="flex items-center gap-4">
                    <div
                      className="w-16 h-12 rounded flex-shrink-0 flex items-center justify-center border border-border/50"
                      style={{ backgroundColor: `hsl(${entry.thumbnailHue}, 30%, 8%)` }}
                    >
                      <Play size={14} className="text-primary/40" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-[11px] text-foreground">{entry.date}</span>
                        <span className="font-mono text-[9px] text-muted-foreground">{entry.size}</span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-[10px] text-muted-foreground">{new Date(entry.startTime).toLocaleTimeString()}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">{formatDuration(entry.duration)}</span>
                        {entry.detections > 0 && (
                          <div className="flex items-center gap-1">
                            <User size={9} className="text-accent" />
                            <span className="font-mono text-[9px] text-accent">{entry.detections}건</span>
                          </div>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={e => { e.stopPropagation(); handlePlay(entry); }}
                      className="flex-shrink-0 w-8 h-8 rounded-full border border-primary/30 bg-primary/10 hover:bg-primary/20 flex items-center justify-center text-primary transition-colors"
                    >
                      <Play size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

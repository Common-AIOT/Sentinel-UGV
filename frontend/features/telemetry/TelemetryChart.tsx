"use client";

import type { TelemetryPoint } from "@/lib/api";

/**
 * 시계열 SVG 폴리라인 그래프. 이력·실시간이 같은 컴포넌트를 쓴다(S15P11A301-169).
 *
 * 차트 라이브러리 없이 SVG 로 그린다(MVP 결정). null 은 결측으로 취급해 선을 끊는다 —
 * ESP32 미연동 배터리처럼 "없는 값" 을 0 으로 그리면 거짓 그래프가 된다.
 */

interface Props {
  points: TelemetryPoint[];
  /** TelemetryPoint 의 숫자 필드 중 그릴 지표. 기본 CPU 사용률. */
  metric?: "cpu" | "gpu" | "memory" | "jetsonTemp" | "battery";
  label?: string;
  unit?: string;
}

// 보조 지표라 낮게 그린다 — 발견·영상이 주인공인 화면에서 자리를 넓게 쓰면 안 된다.
const W = 320;
const H = 44;
const PAD = 3;

export default function TelemetryChart({
  points,
  metric = "cpu",
  label = "CPU",
  unit = "%",
}: Props) {
  const values = points.map(p => p[metric]);
  const known = values.filter((v): v is number => v !== null);

  if (points.length === 0 || known.length === 0) {
    return (
      <div className="border border-border rounded bg-card px-3 py-4 text-center h-full flex items-center justify-center">
        <p className="font-mono text-[10px] text-muted-foreground">
          {label} 데이터가 없습니다
        </p>
      </div>
    );
  }

  const min = Math.min(...known);
  const max = Math.max(...known);
  const span = max - min || 1;
  const yOf = (v: number) => H - PAD - ((v - min) / span) * (H - PAD * 2);
  const xOf = (i: number) =>
    points.length === 1 ? W / 2 : PAD + (i / (points.length - 1)) * (W - PAD * 2);

  // null 에서 선을 끊는다 — 연속 구간(segment)별 polyline.
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((v, i) => {
    if (v === null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));

  const last = known[known.length - 1];
  const fmtT = (iso: string) =>
    new Date(iso).toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit" });

  return (
    // h-full flex — 그리드에서 옆 칸(요약 카드 2×2)과 같은 높이로 늘어나 빈틈 없이 채운다.
    <div className="border border-border rounded bg-card px-3 py-2.5 h-full flex flex-col">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="font-mono text-[10px] text-muted-foreground uppercase tracking-wider">{label}</span>
        <span className="font-mono text-xs text-foreground tabular-nums">
          {last.toFixed(1)}
          <span className="text-[9px] text-muted-foreground font-normal"> {unit}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full flex-1" style={{ minHeight: H }} preserveAspectRatio="none">
        {segments.map((pts, i) => (
          <polyline
            key={i}
            points={pts}
            fill="none"
            stroke="var(--chart-1)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {/* 단일 점(폴리라인 불가) 구간 표시 */}
        {segments.length === 0 && known.length >= 1 && (
          <circle cx={W / 2} cy={yOf(last)} r="2" fill="var(--chart-1)" />
        )}
      </svg>
      <div className="flex justify-between mt-1">
        <span className="font-mono text-[9px] text-muted-foreground/60">{fmtT(points[0].time)}</span>
        <span className="font-mono text-[9px] text-muted-foreground/60">
          {min.toFixed(0)}–{max.toFixed(0)} {unit}
        </span>
        <span className="font-mono text-[9px] text-muted-foreground/60">{fmtT(points[points.length - 1].time)}</span>
      </div>
    </div>
  );
}

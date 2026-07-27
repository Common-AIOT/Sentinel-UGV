"use client";

import { useState } from "react";
import {
  AlertTriangle,
  Maximize2,
  Minimize2,
  RefreshCw,
  Settings,
  Signal,
} from "lucide-react";

import { useStreaming } from "./StreamingContext";
import { isPathConfigured } from "./useWhepStream";
import type { StreamPath, StreamState, WhepStatus } from "./useWhepStream";

/**
 * 관제 영상 패널 (S15P11A301-107).
 *
 * S15P11A301-106의 WHEP 엔드포인트에서 받은 H.264 스트림을 재생하고 연결
 * 상태를 표시한다. 상태 표시는 UX가 아니라 안전 기능이다(SR-010).
 */

const STATE_LABEL: Record<StreamState, string> = {
  CONNECTING: "연결 중",
  LIVE: "실시간",
  DEGRADED: "품질 저하",
  RECONNECTING: "재연결 중",
  OFFLINE: "오프라인",
};

/** LIVE만 초록, DEGRADED는 경고색, 나머지는 위험색으로 구분한다. */
const STATE_DOT: Record<StreamState, string> = {
  CONNECTING: "bg-muted-foreground animate-pulse",
  LIVE: "bg-primary animate-pulse",
  DEGRADED: "bg-accent animate-pulse",
  RECONNECTING: "bg-accent animate-pulse",
  OFFLINE: "bg-destructive",
};

/**
 * 상세 지표 툴팁.
 *
 * 배지에 다 넣으면 읽기 어려우므로 조종 판단에 바로 쓰는 값만 배지에 두고
 * 나머지는 여기에 둔다. freezeCount는 브라우저가 집계한 것이고 32-4의
 * 정지 판정과는 별개 지표다.
 */
function statsTooltip(status: WhepStatus): string {
  const lines = [`상태 ${STATE_LABEL[status.state]}`, `경로 ${status.path}`];
  if (status.width !== null && status.height !== null) {
    lines.push(`해상도 ${status.width}×${status.height}`);
  }
  if (status.fps !== null) lines.push(`디코딩 ${status.fps.toFixed(1)} fps`);
  if (status.bitrateKbps !== null) {
    lines.push(`수신 ${status.bitrateKbps.toFixed(0)} kbps`);
  }
  if (status.lossPct !== null && status.packetsLost !== null) {
    lines.push(`패킷 손실 ${status.lossPct.toFixed(2)}% (${status.packetsLost}개)`);
  }
  if (status.rttMs !== null) lines.push(`RTT ${status.rttMs.toFixed(1)} ms`);
  if (status.jitterMs !== null) lines.push(`지터 ${status.jitterMs.toFixed(1)} ms`);
  if (status.freezeCount !== null) {
    const sec = status.freezeSeconds ?? 0;
    lines.push(`브라우저 정지 ${status.freezeCount}회 / ${sec.toFixed(1)}초`);
  }
  if (status.receiveLatencyMs !== null) {
    lines.push(`수신 지연 ${Math.round(status.receiveLatencyMs)} ms`);
  }
  if (status.processingMs !== null) {
    lines.push(`  처리(수신~디코딩) ${status.processingMs.toFixed(1)} ms`);
  }
  if (status.latencyMs !== null) {
    lines.push(`  버퍼 대기 ${Math.round(status.latencyMs)} ms`);
  }
  if (status.rttMs !== null) {
    lines.push(`  네트워크 편도 ${(status.rttMs / 2).toFixed(1)} ms`);
  }
  lines.push("");
  lines.push(
    "수신 지연은 네트워크 편도와 브라우저의 수신~디코딩 처리 시간을 합한 값이다. Jetson 쪽 카메라 노출과 인코딩은 브라우저가 알 수 없어 빠져 있으므로, 실제 glass-to-glass 지연은 이보다 크다. VID-01은 타임코드 촬영으로 측정한다.",
  );
  return lines.join("\n");
}

interface VideoPanelProps {
  isMain?: boolean;
  onSwap: () => void;
}

export default function VideoPanel({ isMain = false, onSwap }: VideoPanelProps) {
  const { attachVideo, status, path, setPath, reconnectNow } = useStreaming();
  const [showSettings, setShowSettings] = useState(false);

  const showVideo = status.state === "LIVE" || status.state === "DEGRADED";

  return (
    <div
      className={`relative bg-[#10161d] border-b border-border flex-shrink-0 group ${
        isMain ? "flex-1 min-h-0" : ""
      }`}
      style={isMain ? {} : { height: 180 }}
    >
      <video
        ref={attachVideo}
        autoPlay
        muted
        playsInline
        className="w-full h-full object-cover"
      />

      {!showVideo && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#10161d] gap-2">
          <Signal
            size={isMain ? 28 : 20}
            className="text-destructive opacity-60"
          />
          <span className="font-mono text-[10px] text-destructive/60 tracking-widest">
            {STATE_LABEL[status.state]}
          </span>
          {status.error && (
            <span className="font-mono text-[9px] text-muted-foreground max-w-[80%] text-center">
              {status.error}
            </span>
          )}
        </div>
      )}

      {/* 정지 경고 2단계. 1초는 노란색, 3초는 빨간색이며 전진이 차단된다. */}
      {showVideo && status.stalledWarn && (
        <div
          className={`absolute inset-x-0 top-0 flex items-center justify-center gap-1.5 py-1 ${
            status.stalledBlock ? "bg-destructive/85" : "bg-accent/80"
          }`}
        >
          <AlertTriangle size={11} className="text-black/80" />
          <span className="font-mono text-[10px] text-black/90 font-bold">
            {status.stalledBlock
              ? `영상 정지 ${((status.staleMs ?? 0) / 1000).toFixed(1)}초 · 수동 전진 차단`
              : `영상 정지 ${((status.staleMs ?? 0) / 1000).toFixed(1)}초`}
          </span>
        </div>
      )}

      {/* 하단 지표.
          LIVE일 때는 상태 문자열을 빼고 실제 수치를 보여준다. 점 색이 이미
          상태를 말하고, 정지·재연결은 배너와 중앙 오버레이가 알리므로
          "실시간"은 자리만 차지한다.
          지연은 "버퍼"로 라벨을 명시한다. jitter buffer + RTT/2이며 카메라
          노출·인코딩·디코딩·표시 지연이 빠져 있어 실제 체감 지연보다 작다.
          그냥 "ms"로 적으면 VID-01 기준을 통과했다고 오해한다. */}
      <div className="absolute bottom-1.5 left-1.5 flex items-center gap-1.5">
        <div className={`w-1.5 h-1.5 rounded-full ${STATE_DOT[status.state]}`} />
        <span
          className="font-mono text-[9px] text-muted-foreground bg-black/50 px-1.5 py-0.5 rounded"
          title={statsTooltip(status)}
        >
          {showVideo ? (
            <>
              {path}
              {status.width !== null &&
                status.height !== null &&
                ` · ${status.width}×${status.height}`}
              {status.fps !== null && ` · ${status.fps.toFixed(1)}fps`}
              {status.bitrateKbps !== null &&
                ` · ${(status.bitrateKbps / 1000).toFixed(2)}Mbps`}
              {status.receiveLatencyMs !== null &&
                ` · 지연 ${Math.round(status.receiveLatencyMs)}ms`}
            </>
          ) : (
            `${STATE_LABEL[status.state]} · ${path}`
          )}
        </span>
      </div>

      {/* 버튼은 항상 보이되 hover에서 선명해진다. hover에만 나타나면
          전환 기능이 있다는 것을 알기 어렵다. */}
      <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-60 group-hover:opacity-100 transition-opacity">
        <button
          onClick={reconnectNow}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title="다시 연결"
        >
          <RefreshCw size={11} />
        </button>
        <button
          onClick={() => setShowSettings((s) => !s)}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title="스트림 경로 설정"
        >
          <Settings size={11} />
        </button>
        <button
          onClick={onSwap}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title={isMain ? "사이드바로 축소" : "메인 화면으로 확대"}
        >
          {isMain ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
        </button>
      </div>

      {showSettings && (
        <div className="absolute top-8 right-1.5 bg-card border border-border rounded p-2 z-10 space-y-1 min-w-[150px]">
          <p className="font-mono text-[9px] text-muted-foreground uppercase tracking-wider mb-1.5">
            스트림 경로
          </p>
          {(
            [
              ["LOCAL", "로컬 (필수)"],
              ["REMOTE", "원격 (선택)"],
            ] as [StreamPath, string][]
          ).map(([value, label]) => {
            // 엔드포인트가 없는 경로는 고를 수 없게 한다. 고르면 무조건
            // OFFLINE이 되므로 선택지로 두면 오해만 만든다.
            const configured = isPathConfigured(value);
            return (
              <button
                key={value}
                disabled={!configured}
                onClick={() => {
                  setPath(value);
                  setShowSettings(false);
                }}
                title={configured ? undefined : "엔드포인트 환경변수가 설정되지 않았다"}
                className={`w-full text-left font-mono text-[10px] px-2 py-1 rounded transition-colors ${
                  !configured
                    ? "text-muted-foreground/35 cursor-not-allowed"
                    : path === value
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                }`}
              >
                {label}
                {path === value && " ✓"}
                {!configured && " (미설정)"}
              </button>
            );
          })}
          <p className="font-mono text-[8px] text-muted-foreground pt-1 leading-relaxed">
            자동 전환은 지연이 갑자기 달라져 조작 판단을 흐리므로 지원하지
            않는다.
          </p>
        </div>
      )}
    </div>
  );
}

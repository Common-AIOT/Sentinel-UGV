"use client";

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Maximize2,
  Minimize2,
  RefreshCw,
  Signal,
} from "lucide-react";

import { useRobot } from "@/features/robot/RobotContext";
import { OverlayLine, OverlayStack } from "@/features/telemetry/PanelOverlay";
import { useStreaming } from "./StreamingContext";
import type { StreamState, WhepStatus } from "./useWhepStream";

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
const STATE_TONE: Record<StreamState, "ok" | "warn" | "bad" | "idle"> = {
  CONNECTING: "idle",
  LIVE: "ok",
  DEGRADED: "warn",
  RECONNECTING: "warn",
  OFFLINE: "bad",
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

/**
 * 탐지 수 오버레이 줄 (S15P11A301-200).
 *
 * 상단 바 배지에서 옮겼다. 그쪽에는 "임무 이력" 링크와 같은 모양의 배지가
 * 나란히 있어서 서로 다른 내용이라는 것이 읽히지 않았다. 관측값이므로 영상
 * 위, 스트림 통계 바로 아래가 제자리다.
 *
 * 새 발견이 오면 2.5초 강조한다. 팝업(제거됨, S15P11A301-196)처럼 영상을
 * 가리지 않으면서, 조용히 숫자만 바뀌어 발표 중 아무도 못 보는 것도 막는다.
 * 첫 렌더의 0에서 N으로 뛰는 것(새로고침 복구)은 강조하지 않는다.
 *
 * 값의 출처는 실 임무의 encounter 폴링뿐이다 — 목업 탐지는 없앴다.
 */
function DetectionLine() {
  const { detections } = useRobot();
  const [flash, setFlash] = useState(false);
  const prev = useRef(0);

  useEffect(() => {
    if (detections.length > prev.current && prev.current > 0) {
      setFlash(true);
      const timer = setTimeout(() => setFlash(false), 2500);
      prev.current = detections.length;
      return () => clearTimeout(timer);
    }
    prev.current = detections.length;
  }, [detections.length]);

  const latest = detections[0];
  return (
    <OverlayLine
      kind="탐지"
      tone={detections.length > 0 ? "warn" : "idle"}
      flash={flash}
      title="임무 중 확정된 발견 수. 상세는 임무 이력에서 본다."
    >
      {detections.length === 0
        ? "없음"
        : `${detections.length}명 · 최근 ${new Date(latest.timestamp).toLocaleTimeString("ko-KR", { hour12: false })}`}
    </OverlayLine>
  );
}

interface VideoPanelProps {
  isMain?: boolean;
  onSwap: () => void;
}

export default function VideoPanel({ isMain = false, onSwap }: VideoPanelProps) {
  // 스트림 경로 설정(톱니 버튼과 로컬/원격 팝오버)을 뺐다 (S15P11A301-223).
  //
  // 선택지가 실질적으로 하나였다. NEXT_PUBLIC_REMOTE_STREAM_URL 이 배포 환경에
  // 없어 원격은 항상 "(미설정)"으로 비활성이었고, 고를 수 없는 항목을 위해
  // 버튼이 영상 위 자리를 차지했다. 작은 화면에서는 그 버튼들이 가려지는 문제도
  // 있었다(S15P11A301-210).
  //
  // 명세도 같은 판단이다 — 원격 중계는 선택 기능이고 기능 축소 순서에서 가장
  // 먼저 버리는 항목이다(요약표 12행, 32-4). 필요해지면 StreamingContext 의
  // setPath 가 그대로 있으므로 되살릴 수 있다.
  const { attachVideo, status, path, reconnectNow } = useStreaming();

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

      {/* 좌측 상단 오버레이 (S15P11A301-200).
          하단에 있던 것을 올렸다 — 아래쪽은 사람·바닥이 찍히는 영역이라 정보가
          영상을 가렸다. 미니맵 라벨과 같은 형식(OverlayLine)을 쓴다.

          LIVE일 때는 상태 문자열을 빼고 실제 수치를 보여준다. 점 색이 이미
          상태를 말하고, 정지·재연결은 배너와 중앙 오버레이가 알린다.
          지연은 수신 지연이며 카메라 노출·인코딩이 빠져 있어 체감보다 작다 —
          자세한 것은 툴팁에 있다. VID-01은 타임코드 촬영으로 측정한다.

          ## 사이드바에서는 경로만 남긴다 (S15P11A301-212)

          폭이 308px이라 수치를 모두 넣으면 줄이 두 줄로 접히면서 우측 상단
          버튼(다시 연결·설정·확대)을 덮는다. 접히게 만드는 것은 해상도·fps·
          비트레이트·지연이므로 그것만 메인으로 제한하고 경로는 남긴다.

          S15P11A301-210에서는 스택 전체를 숨겼는데, 그러면 지금 보고 있는
          영상이 로컬인지 원격인지 화면에서 알 수 없다. 줄을 짧게 줄이는 것과
          없애는 것을 혼동한 것이다.

          재생 중이 아니면 사이드바에서도 상태를 붙인다. `연결 중 · LOCAL`은
          짧아서 접히지 않고, 검은 화면의 이유를 알려준다.

          탐지 줄은 메인에만 둔다. 발견 시각이 붙어 길고, 사이드바는 참조
          화면이다. 툴팁은 양쪽 모두 유지해 상세 지표를 볼 경로를 남긴다. */}
      <OverlayStack>
        <OverlayLine
          kind="STREAM"
          tone={STATE_TONE[status.state]}
          title={statsTooltip(status)}
        >
          {!showVideo ? (
            `${STATE_LABEL[status.state]} · ${path}`
          ) : isMain ? (
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
            path
          )}
        </OverlayLine>
        {isMain && <DetectionLine />}
      </OverlayStack>

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
          onClick={onSwap}
          className="p-1 bg-black/70 hover:bg-black/90 rounded text-muted-foreground hover:text-primary transition-colors"
          title={isMain ? "사이드바로 축소" : "메인 화면으로 확대"}
        >
          {isMain ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
        </button>
      </div>

    </div>
  );
}

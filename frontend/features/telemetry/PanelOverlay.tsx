"use client";

/**
 * 패널 오버레이 줄 (S15P11A301-200).
 *
 * 영상·미니맵 위에 얹는 관측값 표시를 한 형식으로 통일한다. 이전에는 영상
 * 통계가 하단 좌측, 미니맵 라벨이 상단 좌측에 서로 다른 모양으로 있어서 같은
 * 종류의 정보인지 알 수 없었다.
 *
 *   ● STREAM · LOCAL · 1280×720 · 15.0fps · 2.68Mbps · 지연 19ms
 *   ● 탐지 3명 · 최근 16:42:31
 *   ● LIDAR
 *
 * 규칙 두 가지.
 *
 * **위치는 좌측 상단이다.** 하단은 영상 자체(사람·바닥)를 가리는 일이 많고,
 * 우측 상단에는 이미 조작 버튼이 있다.
 *
 * **조작은 넣지 않는다.** 이 오버레이는 관측값 전용이다. 버튼을 섞으면 "영상
 * 위 정보"와 "조작"의 구분이 사라진다 — 운행 모드 토글을 여기 두지 않고 우측
 * 상태 패널에 둔 이유다.
 */

export type OverlayTone = "ok" | "warn" | "bad" | "idle";

const DOT: Record<OverlayTone, string> = {
  ok: "bg-primary animate-pulse",
  warn: "bg-accent animate-pulse",
  bad: "bg-destructive",
  idle: "bg-muted-foreground",
};

interface OverlayLineProps {
  /** 패널 종류. 대문자 짧은 낱말로 둔다 — STREAM, LIDAR. */
  kind?: string;
  tone?: OverlayTone;
  children: React.ReactNode;
  /** 새 값이 들어온 직후 잠깐 강조한다. 탐지 수 증가에 쓴다. */
  flash?: boolean;
  title?: string;
}

export function OverlayLine({
  kind,
  tone = "idle",
  children,
  flash = false,
  title,
}: OverlayLineProps) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${DOT[tone]}`} />
      <span
        /* 배경 대비는 OverlayStack 의 그라디언트가 만든다. 여기서는 옅게만 깔아
           두 줄이 겹쳤을 때 경계가 보이게 한다 (S15P11A301-303). */
        className={`font-mono text-[10px] px-1.5 py-0.5 rounded transition-colors ${
          flash
            ? "bg-accent/25 text-accent"
            : "bg-black/30 text-foreground/80"
        }`}
        title={title}
      >
        {kind && <span className="text-foreground/70">{kind} · </span>}
        {children}
      </span>
    </div>
  );
}

/**
 * 오버레이 줄들을 담는 좌측 상단 컨테이너.
 *
 * 상단에 어두운 그라디언트를 깐다 (S15P11A301-303). 종전에는 줄마다 반투명 검정
 * 배경(`bg-black/50`)만 있었는데, 밝은 천장·형광등이 찍힌 프레임에서는 그 정도로
 * 글씨가 뜨지 않았다 — **영상이 밝을수록 읽기 어려워지는** 표시였다. 위에서 아래로
 * 사라지는 그라디언트는 영상 내용을 거의 가리지 않으면서 대비를 만든다.
 *
 * 그라디언트는 폭 전체를 덮되 `pointer-events-none` 이라 아래의 영상 조작(우측 상단
 * 버튼)을 막지 않는다.
 */
export function OverlayStack({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="absolute top-0 left-0 right-0 h-16 z-10 pointer-events-none
                      bg-gradient-to-b from-black/70 via-black/25 to-transparent" />
      <div className="absolute top-2 left-2 flex flex-col gap-1 z-10 pointer-events-none">
        {/* title 툴팁을 보려면 포인터가 닿아야 하므로 자식만 이벤트를 받는다. */}
        <div className="flex flex-col gap-1 pointer-events-auto items-start">
          {children}
        </div>
      </div>
    </>
  );
}

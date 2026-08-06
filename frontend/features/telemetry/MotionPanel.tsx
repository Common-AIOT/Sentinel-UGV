"use client";

import { Gauge } from "lucide-react";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 주행 지표 (S15P11A301-300).
 *
 * 속도는 v2.0-r3(S15P11A301-214)에서 뺐던 값인데 다시 넣는다(팀 요청). 그 결정의
 * 근거("관제자의 행동을 바꾸지 않는다")는 여전히 맞지만, 명세가 대안으로 말한
 * "임무 이력 그래프에는 남긴다"가 **실제로 구현된 적이 없어** 속도를 볼 곳이 화면
 * 어디에도 없었다.
 *
 * 원천은 후륜 MT6701 엔코더 2개(ESP32 계수)를 젯슨이 역산한 오도메트리다 —
 * 라이다가 아니다. 그래서 센서 보드가 빠지면 온습도와 함께 빈다. 라이다 SLAM 이
 * 추정하는 위치는 그때도 오므로 **"지도에서는 로봇이 움직이는데 속도는 —"** 이
 * 정상 조합이고, 결측 안내가 그 사실을 말해야 한다.
 *
 * 값 범위는 로봇이 강제한다(24.2, 상한 0.25 m/s). 관제자에게 조절 수단이 없으므로
 * 임계 경고를 만들지 않는다.
 */
function Row({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[11px] text-muted-foreground flex-shrink-0">{label}</span>
      <span className="font-mono text-xs tabular-nums text-foreground">
        {/* `== null` 은 undefined 도 함께 잡는다. 응답에 키가 없는 경우(구 백엔드)에도
            화면이 죽지 않아야 한다 — 결측 표시는 값이 없다는 뜻이지 오류가 아니다. */}
        {value == null ? "—" : value.toFixed(2)}
        <span className="text-[10px] text-muted-foreground font-normal"> {unit}</span>
      </span>
    </div>
  );
}

export default function MotionPanel() {
  const { motion } = useRobot();
  const noReading = motion.linearVelocity == null && motion.angularVelocity == null;

  return (
    <div className="p-3.5 space-y-2.5 border-t border-border">
      <div className="flex items-center gap-1.5">
        <Gauge size={12} className="text-muted-foreground" />
        <span className="text-[11px] font-medium text-muted-foreground">주행</span>
      </div>
      <div className="space-y-1.5">
        <Row label="속도" value={motion.linearVelocity} unit="m/s" />
        <Row label="회전 속도" value={motion.angularVelocity} unit="rad/s" />
      </div>
      {noReading && (
        // 값이 없으면 이유를 말한다 — 침묵하면 고장인지 대기인지 알 수 없다.
        // 엔코더는 센서 보드에 딸려 있으므로 온습도가 함께 비어 있으면 같은 원인이다.
        <p className="text-[10px] text-muted-foreground/70">
          엔코더 값 없음 — 센서 보드(ESP32) 연결을 확인하세요
        </p>
      )}
    </div>
  );
}

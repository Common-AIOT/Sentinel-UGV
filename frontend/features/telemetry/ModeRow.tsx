"use client";

import { Cpu, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 운행 모드 한 줄 (S15P11A301-200).
 *
 * 이전에는 화면 하단 조종 바가 세로 140px을 고정으로 차지했고, `object-cover`인
 * 영상이 남은 공간에 맞춰 잘렸다 — 720p가 깨져 보인 원인이다. 조종 수단이
 * 모바일 앱으로 정해져 관제 웹에 조이스틱 자리가 필요 없으므로, 상태 패널
 * 위 한 줄로 줄인다. **영상 높이를 건드리지 않는다.**
 *
 * 오버레이(영상 위)에 두지 않은 것은 의도다. 그쪽은 관측값 전용이고 여기는
 * 조작이다. 섞으면 "보는 것"과 "누르는 것"의 구분이 사라진다.
 *
 * 수동은 조종 권한을 모바일 앱에 넘긴다는 뜻이다. 관제 웹에는 조종 입력이
 * 없으므로 진입 시 그 사실을 알린다 — 예전 게임패드 검사가 있던 자리다.
 *
 * **이 토글은 아직 로봇에게 아무것도 알리지 않는다.** sendCommand의 manual/auto
 * 는 서버 명령이 없어 화면 상태만 바꾼다(RobotContext 주석, 제어 세션은
 * S15P11A301-39 범위). 즉 지금은 "관제자가 지금 어느 모드로 보고 있다"는 표시에
 * 가깝다. 실제 권한 이관은 control-session API와 젯슨의 cmd/drive 구독이 붙어야
 * 성립한다 — 그때까지 이 화면만 보고 로봇이 수동으로 넘어갔다고 판단하면 안 된다.
 */
export default function ModeRow() {
  const { status, sendCommand } = useRobot();
  const manual = status.controlMode === "MANUAL";

  const toggle = async () => {
    if (manual) {
      await sendCommand("auto");
      return;
    }
    await sendCommand("manual");
    // 이 전환은 표시만 바꾼다. 자율 주행이 돌고 있으면 계속 돈다 — 그 사실을
    // 알리지 않으면 조작자가 "이제 내가 조종한다"고 오해한다. 실제 권한 이관은
    // 제어 세션(S15P11A301-39)이 붙어야 성립한다.
    toast("수동 조종 표시로 바꿨습니다", {
      description:
        "조종은 모바일 앱에서 합니다. 이 전환은 표시만 바꾸며 진행 중인 자율 주행을 멈추지 않습니다 — 멈추려면 일시정지를 쓰세요.",
      duration: 7000,
    });
  };

  return (
    <div className="px-3.5 py-2.5 border-b border-border flex items-center justify-between gap-2">
      <span className="text-[11px] font-medium text-muted-foreground flex-shrink-0">
        운행 모드
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={manual}
        aria-label="운행 모드 — 자율 주행과 수동 조종 전환"
        onClick={toggle}
        className="relative flex rounded-md border border-border bg-background p-0.5 cursor-pointer"
        style={{ width: 148 }}
      >
        <div
          className={`absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded transition-all duration-200 ${
            manual
              ? "left-[calc(50%+1px)] bg-accent/20 border border-accent/40"
              : "left-0.5 bg-primary/15 border border-primary/30"
          }`}
        />
        <div
          className={`relative flex-1 flex items-center justify-center gap-1 py-1 z-10 transition-colors ${
            !manual ? "text-primary" : "text-muted-foreground"
          }`}
        >
          <Cpu size={11} />
          <span className="text-[10px] font-medium">자율</span>
        </div>
        <div
          className={`relative flex-1 flex items-center justify-center gap-1 py-1 z-10 transition-colors ${
            manual ? "text-accent" : "text-muted-foreground"
          }`}
        >
          <Smartphone size={11} />
          <span className="text-[10px] font-medium">수동</span>
        </div>
      </button>
    </div>
  );
}

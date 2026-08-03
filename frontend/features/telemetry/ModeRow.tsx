"use client";

import { Cpu, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 운행 모드 한 줄 (S15P11A301-200·210).
 *
 * 이전에는 화면 하단 조종 바가 세로 140px을 고정으로 차지했고, `object-cover`인
 * 영상이 남은 공간에 맞춰 잘렸다 — 720p가 깨져 보인 원인이다. 조종 수단이
 * 모바일 앱으로 정해져 관제 웹에 조이스틱 자리가 필요 없으므로, 상태 패널
 * 위 한 줄로 줄인다. **영상 높이를 건드리지 않는다.**
 *
 * 오버레이(영상 위)에 두지 않은 것은 의도다. 그쪽은 관측값 전용이고 여기는
 * 조작이다. 섞으면 "보는 것"과 "누르는 것"의 구분이 사라진다.
 *
 * ## 수동 진입이 자율 주행을 멈춘다
 *
 * 모바일 앱은 모터 ESP32에 직접 붙어 조종한다 — 젯슨을 거치지 않는다. 그래서
 * 젯슨은 앱의 존재를 모르고, 자율 주행이 돌고 있으면 **두 쪽이 같은 모터를
 * 동시에 밀게 된다.** 원래 그것을 막는 `command_mux_node`(§34-11)가 있어야
 * 하는데 없고, 앱이 젯슨을 우회하므로 mux로도 막을 수 없다. 끄는 쪽이 유일한
 * 방법이다.
 *
 * 그래서 수동을 누르면 실제 PAUSE 명령을 보낸다. 표시만 바꾸는 토글이었을 때는
 * 조작자가 "이제 내가 조종한다"고 믿는 동안 로봇이 계속 자율로 달렸다.
 *
 * ## 자율 복귀는 재개하지 않는다
 *
 * 자율을 누르면 모드 표시만 되돌리고 주행을 재개하지 않는다. 26.3과 SR-008이
 * "재개는 운영자의 명시적 명령으로만"과 자동 재출발 금지를 정했다 — 토글을
 * 되돌렸다는 이유로 로봇이 갑자기 움직이면 그것이 사고다. 재개는 상태 패널의
 * 「탐사 재개」로 한다(PAUSED일 때 그 버튼과 안내가 나온다).
 *
 * 그래서 두 방향이 비대칭이다. 의도한 것이다 — 멈추는 것은 즉시, 움직이는 것은
 * 명시적으로.
 */
export default function ModeRow() {
  const { status, sendCommand, missionId } = useRobot();
  const manual = status.controlMode === "MANUAL";

  // 서버가 아는 주행 상태만 본다(SERVER_MISSION_STATE 5종). 이 둘이 아니면
  // 이미 멈춰 있으므로 PAUSE를 보낼 이유가 없다.
  const driving =
    status.missionState === "EXPLORING" || status.missionState === "RETURNING";

  const toggle = async () => {
    if (manual) {
      await sendCommand("auto");
      if (missionId && status.missionState === "PAUSED") {
        toast("자율 주행 모드로 되돌렸습니다", {
          description:
            "주행은 아직 멈춰 있습니다. 재개는 「탐사 재개」로 지시하세요 — 모드 전환만으로 다시 움직이지 않습니다.",
          duration: 6000,
        });
      }
      return;
    }

    // 수동 진입. 주행 중이면 먼저 멈춘다 — 앱과 자율이 동시에 모터를 밀지
    // 않게 하는 것이 이 토글의 실제 기능이다.
    try {
      if (missionId && driving) {
        await sendCommand("pause");
        toast.success("자율 주행을 멈췄습니다", {
          description: "모바일 앱에서 조종하세요. 관제 웹에는 조종 입력이 없습니다.",
          duration: 6000,
        });
      } else {
        toast("수동 조종 모드입니다", {
          description: "모바일 앱에서 조종하세요. 진행 중인 자율 주행은 없습니다.",
          duration: 5000,
        });
      }
    } catch {
      // 정지 명령이 실패하면 수동으로 넘어가면 안 된다. 조작자가 조종한다고
      // 믿는 동안 로봇이 자율로 계속 달리는 상태가 가장 위험하다.
      toast.error("자율 주행을 멈추지 못했습니다", {
        description:
          "수동으로 전환하지 않았습니다. 연결을 확인하고 다시 시도하거나 「일시정지」를 쓰세요.",
        duration: 8000,
      });
      return;
    }
    await sendCommand("manual");
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
        aria-label="운행 모드 — 자율 주행과 수동 조종 전환. 수동으로 바꾸면 자율 주행이 멈춘다"
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

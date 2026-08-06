"use client";

import { Cpu, Smartphone } from "lucide-react";
import { toast } from "sonner";
import { useRobot } from "@/features/robot/RobotContext";

/**
 * 운행 모드 한 줄 (S15P11A301-200·210).
 *
 * 이전에는 화면 하단 조종 바가 세로 140px을 고정으로 차지했고, `object-cover`인
 * 영상이 남은 공간에 맞춰 잘렸다 — 720p가 깨져 보인 원인이다. 조종 수단이
 * 모바일 앱으로 정해져 관제 웹에 조이스틱 자리가 필요 없으므로 한 줄로 줄였다.
 *
 * **하단 명령 바로 옮겼다 (S15P11A301-303).** 모드 전환은 조작이고, 조작은 이제
 * 하단 바가 모은다 — 사이드바는 관측값만 남는다. 「지금 자율인가 수동인가」와
 * 「무엇을 누를 수 있나」가 한 줄에 있어야 판단이 된다.
 *
 * 오버레이(영상 위)에 두지 않은 것은 의도다. 그쪽은 관측값 전용이다.
 *
 * ## 이제 명령 하나만 보낸다 (S15P11A301-298)
 *
 * 폰이 모터 ESP32에 직결하는 것은 확정·영구 토폴로지다. 달라진 것은 **모터
 * ESP32가 중재자가 되었다**는 점이다 — 수동 패킷을 받는 순간 래치를 잡고 젯슨의
 * `DRIVE_COMMAND` 액추에이션을 거부한다. 즉 "두 쪽이 같은 모터를 동시에 민다"는
 * 위험이 하드웨어에서 닫혔다.
 *
 * 그래서 이 버튼은 **더 이상 PAUSE를 먼저 보내지 않는다.** `MANUAL` 명령 하나를
 * 보내고 젯슨이 `→PAUSED→MANUAL` 2단 전이를 스스로 수행한다(26.3, 14.2). 종전의
 * 두 명령 시퀀스(PAUSE 성공 확인 → manual)는 그 사이에 앱이 조종을 시작하면
 * 순서가 뒤엉키는, **이길 수 없는 경쟁**이었다. 이제 그 창이 없다.
 *
 * ## 자율 복귀는 재개하지 않는다. 그리고 거부될 수 있다.
 *
 * 「자율」의 착지점은 `PAUSED`다. 26.3과 SR-008이 "재개는 운영자의 명시적
 * 명령으로만"과 자동 재출발 금지를 정했다 — 토글을 되돌렸다는 이유로 로봇이
 * 갑자기 움직이면 그것이 사고다. 재개는 상태 패널의 「탐사 재개」로 한다.
 *
 * 또한 「자율」은 **모터 보드가 거부할 수 있는 유일한 명령**이다. 최근 0.5초 안에
 * 모바일 조종 입력이 있었으면 `REJECTED/MANUAL_INPUT_ACTIVE`로 끝난다. 그래서
 * 토스트는 "됐습니다"가 아니라 "요청했습니다"라고 말한다 — 실제 답은
 * `watchCommand`가 CommandAlert로 가져온다.
 */
export default function ModeRow() {
  const { status, sendCommand, missionId } = useRobot();
  const manual = status.controlMode === "MANUAL";

  // ESTOP·ERROR 는 사람이 물리적으로 확인하고 풀어야 하는 상태다(26.5). 젯슨의
  // mode_gateway 도 이 상태에서는 프레임을 아예 보내지 않는다.
  const latched = status.missionState === "ESTOP" || status.missionState === "ERROR";
  // **숨기지 않고 비활성으로 둔다.** 자율로 돌아가는 유일한 길이므로, 사라지면
  // 조작자가 무엇을 눌러야 할지 알 수 없다.
  const disabled = !missionId || latched;

  const toggle = async () => {
    if (disabled) {
      toast(
        latched
          ? "비상·결함 정지 상태에서는 모드를 바꿀 수 없습니다"
          : "진행 중인 임무가 없습니다",
        {
          description: latched
            ? "원인을 확인하고 해제한 뒤 다시 시도하세요."
            : "모드 전환은 임무를 만들지 않습니다. 「탐사 시작」으로 임무를 먼저 시작하세요.",
          duration: 6000,
        },
      );
      return;
    }

    try {
      if (manual) {
        await sendCommand("auto");
        toast("자율 전환을 요청했습니다", {
          description:
            "모바일 조종이 계속되고 있으면 로봇이 거부할 수 있습니다. 주행 재개는 「탐사 재개」로 따로 지시하세요.",
          duration: 6000,
        });
        return;
      }

      await sendCommand("manual");
      toast.success("수동 조종으로 전환합니다", {
        description:
          "모바일 앱에서 조종하세요. 수동 중에는 라이다 정지구역·충돌 감시·소프트 E-Stop이 적용되지 않습니다.",
        duration: 8000,
      });
    } catch {
      toast.error("모드를 바꾸지 못했습니다", {
        description: "명령이 로봇에 전달되지 않았습니다. 연결을 확인하고 다시 시도하세요.",
        duration: 8000,
      });
    }
  };

  return (
    <div className="flex items-center gap-2 flex-shrink-0">
      <span className="text-[11px] font-medium text-muted-foreground flex-shrink-0">
        운행 모드
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={manual}
        aria-disabled={disabled}
        aria-label="운행 모드 — 자율 주행과 수동 조종 전환. 자율로 돌아가는 유일한 경로다"
        onClick={toggle}
        className={`relative flex rounded-md border border-border bg-background p-0.5 ${
          disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"
        }`}
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

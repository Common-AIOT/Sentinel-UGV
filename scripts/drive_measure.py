#!/usr/bin/env python3
"""주행 거리 실측 도구 (S15P11A301-339).

    python3 scripts/drive_measure.py <속도 m/s> <지속 s>
    python3 scripts/drive_measure.py 0.15 6.7

**`drive_burst.py` 를 대체한다.** 그쪽은 `/cmd_vel` 에 쏘는 방식이라 상위 층이
언제든 0 으로 덮었다 — 2026-08-09 시험에서 6.7초 발행 중 비영 구간이 0.60초뿐인
일이 실제로 벌어졌고, 그 측정은 무효였다. 원인은 셋이 겹쳤다.

  * 초음파 보호정지가 `STOP_COMMAND` 를 중계했다(센서 오측이어도 막았다).
    **이것은 2026-08-09 당시의 상태다** — 지금은 펌웨어가 `PROXIMITY_STOP_ENABLED
    = false` 로 발동을 꺼서 이 층은 더 이상 막지 않는다(03장 CTRL-18). 나머지
    둘은 그대로 유효하므로 이 도구의 존재 이유는 변하지 않았다
  * `safety_gate` 가 같은 신호를 **독립적으로** 또 본다 — 끄는 파라미터가 없고,
    토픽을 끊으면 이번엔 `PROXIMITY_STALE` 로 막는다(침묵도 차단 사유다)
  * 자율 주행 중에는 Nav2·탐사가 같은 토픽에 명령을 실어 측정과 섞인다

그래서 이 도구는 **게이트 아래**(`~/drive_command`)로 직접 쏜다. 캘리브레이션은
안전 체인의 판단이 아니라 「명령한 만큼 갔는가」를 보는 작업이라, 중간 층이
값을 바꾸면 측정 자체가 성립하지 않는다.

**안전 체인을 우회하므로 시험 전용이다.** 초음파·Nav2 정지가 걸리지 않는다.
사람이 앞을 비우고 **12V 배터리 분리 담당자가 그 지점에 붙은 상태에서만** 쓴다 —
래칭형 물리 E-Stop 은 도입하지 않았고 하드웨어 차단은 배터리 분리뿐이다
(03장 34-10). 상한(|v| ≤ 0.5, 지속 ≤ 10s)과 종료 시 정지 명령은 그 전제 위의
최소 장치다.

오도메트리 시작·끝을 스스로 찍어 **보고 거리**를 낸다. 줄자 실측과 나란히 놓으면
「엔코더가 맞나」와 「로봇이 갔나」가 한 번에 갈린다.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import String

# 브리지가 그대로 받아 프레임으로 만드는 값(`esp32_motor_bridge_node._on_drive_command`).
BOARD_MODE_AUTO = 2
# 보드 워치독이 300ms 다. 발행 주기가 그보다 넉넉히 짧아야 명령이 끊기지 않는다.
PUBLISH_HZ = 20.0
COMMAND_TIMEOUT_MS = 300

MAX_SPEED_MPS = 0.5
MAX_DURATION_S = 10.0

# ── 펌웨어 제약 (`safety_stub.cpp`) ────────────────────────────────────────
#
# **이 셋을 모르면 로봇이 안 움직이는데 원인을 못 찾는다.** 2026-08-09 에 실제로
# 그랬다 — 명령 61회 중 보드가 PWM 을 켠 것이 2회뿐이었고, 그것을 「우측 모터
# 부호 반전」이라는 펌웨어 결함으로 오진해 티켓까지 냈다(S15P11A301-352).
# 셋 다 의도된 보호 로직이다.
#
# 1) 데드밴드 — |속도| < 150 mm/s 면 PWM 이 0 이다(`mmpsToSignedPwm`).
#    0.15 m/s 는 **경계값 그 자체**라 시험 속도로 부적절하다. 조금만 낮아도
#    아무것도 안 걸린다.
MIN_DRIVE_SPEED_MMPS = 150
# 2) 방향전환 데드타임 — 방향이 바뀌면 500ms 동안 출력을 강제로 끈다
#    (`DIRECTION_CHANGE_STOP_MS`, `holdDriveOutputsOff`). 정지(0) 뒤 곧바로
#    구동을 걸면 여기에 걸린다. 그래서 측정 시작 전에 이만큼 쉰다.
DIRECTION_CHANGE_STOP_MS = 500
# 3) 좌우 역방향 금지 — 좌우가 서로 반대 방향으로 0 이 아니면 **둘 다 0** 으로
#    만든다(조향 링크 보호). 이 도구는 좌우에 같은 값을 넣으므로 걸리지 않는다.

# 데드타임 500ms 에 여유를 얹는다. 이 대기는 측정 창 밖이라 거리에 안 섞인다.
SETTLE_S = 0.8

# ── 경쟁 발행자 (S15P11A301-339) ──────────────────────────────────────────
#
# **`~/drive_command` 는 이 도구의 전용 토픽이 아니다.** `vehicle_kinematics` 가
# `/cmd_vel` 을 변환해 같은 토픽에 20Hz 로 발행하는데, 안전 게이트가 막고 있으면
# 그 값이 **0** 이다. 보드는 마지막에 온 값을 저장하므로 두 발행자가 섞이면
# 명령의 대부분이 0 으로 덮인다.
#
# 2026-08-09 에 이것 때문에 하루를 썼다 — 측정에서 「200 을 보내는데 보드가 0 을
# 저장한다」가 나왔고, 펌웨어 결함으로 오인해 담당자에게 디버그 계측까지 넣게
# 했다(S15P11A301-352 도 같은 뿌리의 오진이다). 실측 비율이 증거였다:
#
#     이 도구 없이 5초 관측:  97프레임 전부 0
#     이 도구와 함께:         224회 0 / 56회 200
#
# 그래서 측정 중에는 경쟁 발행자를 **멈춘다**(SIGSTOP). 죽이지 않는 이유는
# 되살리기 위해서다 — `vehicle_kinematics` 는 launch 가 관리하므로 죽이면 스택을
# 다시 올려야 한다. 멈추지 못하면 **측정을 시작하지 않는다.** 오염된 숫자를 내는
# 것보다 아무것도 안 내는 편이 낫다.
COMPETING_PUBLISHER = "sentinel_drive/vehicle_kinematics"


def _competing_pids() -> list[int]:
    """`~/drive_command` 에 함께 쏘는 노드의 PID. 자기 자신은 안 잡는다."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", COMPETING_PUBLISHER],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me = os.getpid()
    return [int(p) for p in out.split() if p.isdigit() and int(p) != me]


def _signal_all(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


# 멈춰 둔 PID. 되살리는 경로가 여럿이라 한 곳에서 관리한다.
#
# **`finally` 하나로는 부족하다.** 그것이 도는 것은 파이썬이 정상적으로 풀려나갈
# 때뿐이라 `Ctrl+Z`(SIGTSTP)·SIGHUP(터미널 종료)·SIGTERM(`kill`)에서는 안 돈다.
# 그러면 `vehicle_kinematics` 가 멈춘 채 남고, 증상은 「자율 주행이 조용히 안 된다」
# 하나이며 원인이 프로세스 상태(T)라 로그에도 안 나온다 — 위 주석이 경고한 바로
# 그 상태를 이 스크립트 자신이 만들 수 있었다.
_paused_pids: list[int] = []


def _resume_paused() -> None:
    """멈춰 둔 발행자를 되살린다. 여러 번 불려도 안전하다."""
    global _paused_pids
    if not _paused_pids:
        return
    pids, _paused_pids = _paused_pids, []
    _signal_all(pids, signal.SIGCONT)
    print(f"경쟁 발행자 복구 완료: {pids}")


def _install_resume_guards() -> None:
    """정상 종료·시그널·중단 어느 경로로 나가도 되살아나게 한다."""
    atexit.register(_resume_paused)

    def _on_terminating(signum, _frame):
        _resume_paused()
        # 기본 동작으로 되돌려 다시 보낸다. 종료 코드가 시그널을 그대로 반영한다.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _on_suspend(_signum, _frame):
        # Ctrl+Z. 우리가 멈추면 되살릴 주체가 없어지므로 먼저 되살린다.
        # 그 순간 경쟁 발행자가 다시 명령을 쏘므로 **이 측정은 무효다** —
        # 재개해서 이어가지 않고 여기서 끝낸다.
        print("\n★ 멈춤 요청. 경쟁 발행자를 되살리고 종료한다 — 이 측정은 무효다.")
        _resume_paused()
        sys.exit(130)

    # 이 도구는 젯슨(리눅스) 전용이지만 시그널을 이름으로 찾는다. 없는 플랫폼에서
    # 설치가 실패하더라도 atexit·finally 는 그대로 살아 있어야 하기 때문이다.
    for name, handler in (
        ("SIGTERM", _on_terminating),
        ("SIGHUP", _on_terminating),
        ("SIGTSTP", _on_suspend),
    ):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass  # 메인 스레드가 아니거나 플랫폼이 막으면 넘어간다.


class Measure(Node):
    def __init__(self, speed_mps: float, duration_s: float) -> None:
        super().__init__("drive_measure")
        self.speed_mps = speed_mps
        self.duration_s = duration_s
        self.pub = self.create_publisher(String, "/esp32_motor_bridge/drive_command", 10)
        self.create_subscription(Odometry, "/wheel/odometry", self._on_odom, 50)
        # 남이 보낸 drive_command 를 센다. 내가 발행한 것은 여기 안 잡힌다 —
        # rclpy 는 자기 발행을 자기 구독으로 돌려주지 않는다.
        self.create_subscription(
            String, "/esp32_motor_bridge/drive_command", self._on_foreign_command, 20
        )
        self.x: float | None = None
        self.samples = 0
        self.commands_seen = 0

    def _on_odom(self, msg: Odometry) -> None:
        self.x = msg.pose.pose.position.x
        self.samples += 1

    def _on_foreign_command(self, _msg: String) -> None:
        self.commands_seen += 1

    def _command(self, mmps: int) -> None:
        self.pub.publish(String(data=json.dumps({
            "mode": BOARD_MODE_AUTO,
            "target_drive_left_mmps": mmps,
            "target_drive_right_mmps": mmps,
            "target_steering_mdeg": 0,
            "max_accel_mmps2": 0,
            "command_timeout_ms": COMMAND_TIMEOUT_MS,
        })))

    def _spin_until(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.02)

    def run(self) -> int:
        # 구독자·오도메트리를 **창 밖에서** 기다린다. 이 대기가 창 안에 들어가면
        # 그 시간만큼 명령 없이 흐른 구간이 거리에 섞인다.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            if self.pub.get_subscription_count() > 0 and self.x is not None:
                break
        if self.pub.get_subscription_count() == 0:
            print("실패: esp32_motor_bridge 가 drive_command 를 구독하지 않는다.")
            print("  브리지가 떠 있는지 확인하라: pgrep -af esp32_motor_bridge")
            return 1
        if self.x is None:
            print("실패: /wheel/odometry 가 오지 않는다 — 센서 브리지를 확인하라.")
            return 1

        mmps = int(round(self.speed_mps * 1000.0))

        # 데드밴드 아래면 보드가 PWM 을 0 으로 만든다 — 아무리 오래 쏴도 안 간다.
        # 조용히 0m 를 보고하는 대신 여기서 막는다.
        if 0 < abs(mmps) < MIN_DRIVE_SPEED_MMPS:
            print(f"실패: {mmps} mm/s 는 펌웨어 데드밴드({MIN_DRIVE_SPEED_MMPS} mm/s) 아래다.")
            print(f"  보드가 PWM 을 0 으로 만든다. {MIN_DRIVE_SPEED_MMPS / 1000:.2f} m/s 이상으로 하라.")
            return 1

        # **경쟁 발행자가 정말 조용해졌는지 확인한다.** 멈췄다고 믿지 않는다 —
        # SIGSTOP 이 늦게 반영되거나 다른 노드가 같은 토픽에 붙어 있을 수 있고,
        # 그러면 측정이 조용히 오염된다(위 상수 주석 참고).
        before = self.commands_seen
        self._spin_until(time.monotonic() + 1.0)
        if self.commands_seen > before:
            print(f"실패: 측정 중 다른 노드가 {COMPETING_PUBLISHER.split('/')[-1]} 로")
            print("  drive_command 를 계속 발행하고 있다. 그 값이 이 도구의 명령을")
            print("  덮어써 측정이 오염된다. 그 노드를 멈추고 다시 시도하라:")
            print(f"    pkill -STOP -f {COMPETING_PUBLISHER}")
            print(f"    (측정 뒤 되살리기: pkill -CONT -f {COMPETING_PUBLISHER})")
            return 1

        # **방향전환 데드타임을 비운다.** 직전 측정의 정지 명령(0) 뒤 곧바로 구동을
        # 걸면 500ms 동안 출력이 강제로 꺼져, 명령을 보내는데도 로봇이 안 움직인다.
        # 이 대기는 창 밖이라 거리에 섞이지 않는다.
        self._spin_until(time.monotonic() + SETTLE_S)

        x0 = self.x
        started = time.monotonic()
        end = started + self.duration_s
        period = 1.0 / PUBLISH_HZ

        while time.monotonic() < end:
            self._command(mmps)
            self._spin_until(min(time.monotonic() + period, end))

        # 정지 명령은 **한 번만** 보낸다. 여러 번 보내면 그 자체가 방향전환으로
        # 잡혀 다음 측정이 데드타임에 걸린다 — 짧은 시험을 반복할 때 로봇이 안
        # 움직이던 원인이 이것이었다(S15P11A301-352). 한 번으로 부족해도 보드
        # 워치독 300ms 가 반드시 세우므로 안전은 그쪽이 보장한다.
        self._command(0)
        # 관성으로 더 구른 몫까지 담기게 잠깐 더 받는다.
        self._spin_until(time.monotonic() + 1.0)

        elapsed = time.monotonic() - started
        reported = (self.x or x0) - x0
        expected = self.speed_mps * self.duration_s

        print(f"발행 창                 {elapsed:.2f} s  ({mmps} mm/s x {self.duration_s:g}s)")
        print(f"명령 기대 거리          {expected:+.3f} m")
        print(f"오도메트리 보고 거리    {reported:+.3f} m   (표본 {self.samples})")
        if abs(expected) > 1e-6:
            print(f"보고/기대 비            {reported / expected:.2f}")
        print()
        print("→ 줄자 실측을 이 둘과 나란히 놓는다.")
        print("   실측 ≈ 기대,  보고 ≈ 실측  이면 정상이다.")
        print("   실측 ≈ 기대인데 보고만 크면 엔코더 스케일·속도 환산을 본다.")
        print("   실측이 기대보다 작으면 모터·전압·정지마찰을 본다.")
        return 0


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    speed = float(sys.argv[1])
    duration = float(sys.argv[2])
    if abs(speed) > MAX_SPEED_MPS or duration > MAX_DURATION_S:
        print(f"★ 안전 상한: |v| ≤ {MAX_SPEED_MPS}, 지속 ≤ {MAX_DURATION_S:g}s")
        sys.exit(1)

    # 경쟁 발행자를 멈춘다. 되살리는 것은 atexit·시그널 핸들러·finally 셋이 함께
    # 보장한다(_install_resume_guards). 멈춘 채로 남으면 vehicle_kinematics 가
    # 죽은 것처럼 보이지 않으면서 자율 주행만 조용히 안 된다.
    paused = _competing_pids()
    if paused:
        _install_resume_guards()
        _paused_pids.extend(paused)
        _signal_all(paused, signal.SIGSTOP)
        print(f"경쟁 발행자 {len(paused)}개를 잠시 멈춘다 (측정 뒤 자동 복구): {paused}")
        # 위 셋이 전부 실패하는 경우(SIGKILL, 전원 차단)를 위해 손으로 되살리는
        # 명령을 미리 찍는다. 그때는 이 줄이 유일한 단서다.
        print(f"  자동 복구가 안 되면: kill -CONT {' '.join(str(p) for p in paused)}")

    rclpy.init()
    node = Measure(speed, duration)
    try:
        code = node.run()
    except KeyboardInterrupt:
        # 사람이 끊었으면 반드시 세운다.
        node._command(0)
        node._spin_until(time.monotonic() + 0.2)
        code = 130
    finally:
        node.destroy_node()
        rclpy.shutdown()
        # **반드시 되살린다.** 멈춘 채로 두면 자율 주행이 조용히 안 되고, 원인이
        # 프로세스 상태(T)라 로그에도 안 나온다. 정상 경로는 여기서 끝나고,
        # 여기까지 못 오는 경로는 atexit 과 시그널 핸들러가 받는다.
        _resume_paused()
    sys.exit(code)


if __name__ == "__main__":
    main()

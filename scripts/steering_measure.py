#!/usr/bin/env python3
"""조향 반경 실측 도구 (S15P11A301-341).

    python3 scripts/steering_measure.py <속도 m/s> <지속 s> <바퀴각 deg>
    python3 scripts/steering_measure.py 0.3 4.0 22     # 좌회전 최대 조향
    python3 scripts/steering_measure.py 0.3 4.0 -22    # 우회전

고정 조향으로 호를 그리게 하고 **회전 반경을 잰다**:

    R = 경로 길이 / yaw 변화    (호의 정의 그대로)

경로 길이는 `/wheel/odometry` 의 (x,y) 누적(틱 기반 — 2026-08-09 검증에서 줄자와
5% 안), yaw 변화는 `/imu/data_raw` 자이로 z 적분이다. 두 값이 **같은 구간**에서
누적되므로 가감속이 섞여도 비가 흔들리지 않는다.

기대값은 자전거 모델이다: R = L / tan(δ),  L = 0.683m (실측).

    δ=22° → R = 1.69m          ← 링키지 비 2.5 반영 시
    δ=22° 지령인데 실제 8.8° → R = 4.41m   ← 1:1 게인(수정 전)

즉 이 도구 한 번으로 「링키지 비가 펌웨어에 반영됐는가」가 갈린다.

`drive_measure.py` 와 같은 이유로 게이트 아래(`~/drive_command`)로 직접 쏘고,
같은 함정들을 지킨다 — 경쟁 발행자(`vehicle_kinematics`) SIGSTOP, 펌웨어
데드밴드(150mm/s), 방향전환 데드타임(500ms). 그쪽 파일 머리의 근거 주석 참고.

**안전 체인을 우회하는 시험 전용이다.** 호 안쪽·바깥쪽을 모두 비워야 한다 —
δ=22° 에서 R≈1.7m 이므로 4초에 좌우로 약 1m 쓸려 나간다. 물리 E-Stop 대기.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu
from std_msgs.msg import String

BOARD_MODE_AUTO = 2
PUBLISH_HZ = 20.0
COMMAND_TIMEOUT_MS = 300

WHEELBASE_M = 0.683          # 실측 (S15P11A301-337/344)
MAX_STEERING_DEG = 22.0      # 실측 δ_max (S15P11A301-341)
MIN_DRIVE_SPEED_MMPS = 150   # 펌웨어 데드밴드 — drive_measure.py 참고
SETTLE_S = 0.8               # 방향전환 데드타임(500ms) + 여유
MAX_SPEED_MPS = 0.5
MAX_DURATION_S = 8.0         # 호 주행이라 직진보다 짧게 잡는다

COMPETING_PUBLISHER = "sentinel_drive/vehicle_kinematics"


def _competing_pids() -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-f", COMPETING_PUBLISHER],
                             capture_output=True, text=True, timeout=5).stdout
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


class SteeringMeasure(Node):
    def __init__(self, speed_mps: float, duration_s: float, steer_deg: float) -> None:
        super().__init__("steering_measure")
        self.speed_mps = speed_mps
        self.duration_s = duration_s
        self.steer_deg = steer_deg
        self.pub = self.create_publisher(String, "/esp32_motor_bridge/drive_command", 10)
        self.create_subscription(Odometry, "/wheel/odometry", self._on_odom, 50)
        self.create_subscription(Imu, "/imu/data_raw", self._on_imu, 100)
        self.create_subscription(
            String, "/esp32_motor_bridge/drive_command", self._on_foreign, 20)
        self.xy: tuple[float, float] | None = None
        self.path_m = 0.0
        self.accumulate = False
        self.yaw_rad = 0.0
        self._last_imu_t: float | None = None
        self.imu_samples = 0
        self.foreign = 0

    def _on_odom(self, msg: Odometry) -> None:
        p = msg.pose.pose.position
        if self.xy is not None and self.accumulate:
            self.path_m += math.hypot(p.x - self.xy[0], p.y - self.xy[1])
        self.xy = (p.x, p.y)

    def _on_imu(self, msg: Imu) -> None:
        # 자이로 z 를 사다리꼴 없이 단순 적분한다. 90Hz 에서 충분하고, 잔여
        # bias(-4.85°/분 실측)는 4초 창에서 0.3° — 판정 대상(수십 도)에 비해 무시.
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self._last_imu_t is not None and self.accumulate:
            dt = t - self._last_imu_t
            if 0 < dt < 0.5:
                self.yaw_rad += msg.angular_velocity.z * dt
                self.imu_samples += 1
        self._last_imu_t = t

    def _on_foreign(self, _msg: String) -> None:
        self.foreign += 1

    def _command(self, mmps: int, steer_mdeg: int) -> None:
        self.pub.publish(String(data=json.dumps({
            "mode": BOARD_MODE_AUTO,
            "target_drive_left_mmps": mmps,
            "target_drive_right_mmps": mmps,
            "target_steering_mdeg": steer_mdeg,
            "max_accel_mmps2": 0,
            "command_timeout_ms": COMMAND_TIMEOUT_MS,
        })))

    def _spin_until(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.02)

    def run(self) -> int:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            if (self.pub.get_subscription_count() > 0 and self.xy is not None
                    and self._last_imu_t is not None):
                break
        if self.pub.get_subscription_count() == 0:
            print("실패: esp32_motor_bridge 가 drive_command 를 구독하지 않는다.")
            return 1
        if self.xy is None or self._last_imu_t is None:
            print("실패: /wheel/odometry 또는 /imu/data_raw 가 오지 않는다.")
            return 1

        mmps = int(round(self.speed_mps * 1000.0))
        steer_mdeg = int(round(self.steer_deg * 1000.0))
        if 0 < abs(mmps) < MIN_DRIVE_SPEED_MMPS:
            print(f"실패: {mmps} mm/s 는 펌웨어 데드밴드({MIN_DRIVE_SPEED_MMPS}) 아래다.")
            return 1

        before = self.foreign
        self._spin_until(time.monotonic() + 1.0)
        if self.foreign > before:
            print("실패: 다른 노드가 drive_command 를 발행 중 — 측정이 오염된다.")
            print(f"    pkill -STOP -f {COMPETING_PUBLISHER}  (뒤에 -CONT 로 복구)")
            return 1

        self._spin_until(time.monotonic() + SETTLE_S)

        # 조향을 먼저 앉힌다. 서보가 목표각으로 가는 동안(rate 제한) 직진이 섞이면
        # 그 구간이 반경을 크게 만든다 — 정지 상태에서는 §34-2 로 서보가 안 움직이니
        # 기는 속도로 1초 굴리며 조향을 세운 뒤 본 측정을 시작한다.
        warm_end = time.monotonic() + 1.0
        while time.monotonic() < warm_end:
            self._command(mmps, steer_mdeg)
            self._spin_until(min(time.monotonic() + 1.0 / PUBLISH_HZ, warm_end))

        self.accumulate = True
        self.path_m = 0.0
        self.yaw_rad = 0.0
        started = time.monotonic()
        end = started + self.duration_s
        while time.monotonic() < end:
            self._command(mmps, steer_mdeg)
            self._spin_until(min(time.monotonic() + 1.0 / PUBLISH_HZ, end))

        self._command(0, steer_mdeg)
        self._spin_until(time.monotonic() + 1.0)   # 관성 몫까지 적분
        self.accumulate = False

        expected_r = WHEELBASE_M / math.tan(math.radians(abs(self.steer_deg)))
        yaw_deg = math.degrees(self.yaw_rad)
        print(f"명령                  {mmps} mm/s, δ={self.steer_deg:+.1f}° x {self.duration_s:g}s")
        print(f"경로 길이(오도메트리)  {self.path_m:.3f} m")
        print(f"yaw 변화(IMU 적분)     {yaw_deg:+.1f}°   (표본 {self.imu_samples})")
        if abs(self.yaw_rad) > math.radians(3):
            measured_r = self.path_m / abs(self.yaw_rad)
            print(f"실측 회전 반경         R = {measured_r:.2f} m")
            print(f"기대 회전 반경         R = {expected_r:.2f} m  (L/tanδ, L={WHEELBASE_M})")
            print(f"실측/기대 비           {measured_r / expected_r:.2f}")
            print()
            print("→ 비 ≈ 1.0 이면 링키지 비 2.5 가 펌웨어에 반영된 것이다.")
            print("   비 ≈ 2.6 (R≈4.4m) 이면 아직 1:1 게인이다 — 플래시를 확인하라.")
        else:
            print("yaw 변화가 3° 미만 — 조향이 걸리지 않았다. 서보 arm·조향 명령 도달을 확인하라.")
        return 0


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    speed = float(sys.argv[1])
    duration = float(sys.argv[2])
    steer = float(sys.argv[3])
    if abs(speed) > MAX_SPEED_MPS or duration > MAX_DURATION_S:
        print(f"★ 안전 상한: |v| ≤ {MAX_SPEED_MPS}, 지속 ≤ {MAX_DURATION_S:g}s")
        sys.exit(1)
    if abs(steer) > MAX_STEERING_DEG:
        print(f"★ 조향 상한: |δ| ≤ {MAX_STEERING_DEG}° (실측 δ_max)")
        sys.exit(1)

    paused = _competing_pids()
    if paused:
        _signal_all(paused, signal.SIGSTOP)
        print(f"경쟁 발행자 {len(paused)}개를 잠시 멈춘다 (측정 뒤 자동 복구): {paused}")

    rclpy.init()
    node = SteeringMeasure(speed, duration, steer)
    try:
        code = node.run()
    except KeyboardInterrupt:
        node._command(0, 0)
        node._spin_until(time.monotonic() + 0.2)
        code = 130
    finally:
        node.destroy_node()
        rclpy.shutdown()
        if paused:
            _signal_all(paused, signal.SIGCONT)
            print(f"경쟁 발행자 복구 완료: {paused}")
    sys.exit(code)


if __name__ == "__main__":
    main()

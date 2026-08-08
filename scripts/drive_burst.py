#!/usr/bin/env python3
"""정밀 주행 버스트: 시작 오버헤드를 타이밍 창 밖으로 뺀다.

사용:  drive_burst.py <속도 m/s> <지속 초>
예:    drive_burst.py 0.25 4     # 전진
       drive_burst.py -0.25 4    # 후진

ros2 topic pub 는 노드 생성 + 디스커버리에 1.7~2.3초를 먹고 그 시간이
timeout 창 안에 들어가 실제 발행 구간이 매번 달랐다. 여기서는
  1. 퍼블리셔를 만들고 /cmd_vel_nav 구독자(command_mux)가 붙을 때까지 대기
  2. 그 다음에야 정확히 <지속 초> 동안 20Hz 발행 (단조 시계 기준)
  3. 0 을 몇 번 쏴서 확실히 세움
  4. /cmd_vel (체인 통과 후) 를 함께 구독해 실제 창과 ∫v·dt 를 보고
"""
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node


class Burst(Node):
    def __init__(self, v: float, dur: float) -> None:
        super().__init__("drive_burst")
        self.v = v
        self.dur = dur
        self.pub = self.create_publisher(Twist, "/cmd_vel_nav", 10)
        self.create_subscription(Twist, "/cmd_vel", self._cv, 50)
        self.first_nz = None
        self.last_nz = None
        self.integ = 0.0
        self.prev = None

    def _cv(self, m: Twist) -> None:
        t = time.monotonic()
        vx = m.linear.x
        if self.prev is not None:
            dt = t - self.prev[0]
            if 0 < dt < 0.5:
                self.integ += self.prev[1] * dt
        self.prev = (t, vx)
        if abs(vx) > 1e-6:
            if self.first_nz is None:
                self.first_nz = t
            self.last_nz = t

    def run(self) -> None:
        # 1. 구독자 대기 — 이 대기가 종전의 "시작 오버헤드"다. 창 밖에서 소화한다.
        t0 = time.monotonic()
        while self.pub.get_subscription_count() == 0:
            if time.monotonic() - t0 > 10:
                print("★ /cmd_vel_nav 구독자가 없다 — command_mux 가 떠 있나?")
                return
            rclpy.spin_once(self, timeout_sec=0.05)
        settle = time.monotonic() - t0
        print(f"구독자 확인 ({settle:.2f}s 소요 — 창 밖)  >>> 발행 시작: {self.v} m/s × {self.dur}s")

        # 2. 정밀 발행 창
        msg = Twist()
        msg.linear.x = float(self.v)
        start = time.monotonic()
        next_t = start
        while True:
            now = time.monotonic()
            if now - start >= self.dur:
                break
            if now >= next_t:
                self.pub.publish(msg)
                next_t += 0.05          # 20Hz
            rclpy.spin_once(self, timeout_sec=0.005)
        actual = time.monotonic() - start

        # 3. 확실한 정지
        stop = Twist()
        for _ in range(6):
            self.pub.publish(stop)
            rclpy.spin_once(self, timeout_sec=0.05)

        # 4. 여운 수집 후 보고
        t_end = time.monotonic()
        while time.monotonic() - t_end < 2.0:
            rclpy.spin_once(self, timeout_sec=0.05)
        print(f"발행 창(퍼블리셔 기준)  {actual:.2f} s   명목 기대 {self.v * self.dur:+.3f} m")
        if self.first_nz is not None:
            print(f"/cmd_vel 비영 구간      {self.last_nz - self.first_nz:.2f} s")
        print(f"명령 기대 거리(∫v·dt)   {self.integ:+.3f} m   <- 실측과 이 값을 비교")


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    v = float(sys.argv[1])
    dur = float(sys.argv[2])
    if abs(v) > 0.5 or dur > 10:
        print("★ 안전 상한: |v| ≤ 0.5, 지속 ≤ 10s")
        sys.exit(1)
    rclpy.init()
    n = Burst(v, dur)
    try:
        n.run()
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()

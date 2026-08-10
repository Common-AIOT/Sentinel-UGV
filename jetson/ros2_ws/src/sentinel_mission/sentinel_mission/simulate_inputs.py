#!/usr/bin/env python3
"""Mission Manager 입력 모사 (S15P11A301-133 검증용).

실제 탐지·주행·음성 스택과 분리해 상태 머신만 결정적으로 회귀 검증하도록
`/perception/person_candidates`와 `/mission/signal`을 직접 발행한다.

`sentinel_recorder`의 `trigger_encounter`와 역할이 다르다. 그쪽은 녹화 노드만
단독으로 시험하기 위해 `/perception/encounter`를 직접 발행한다. 이쪽은 그 상위
경로, 즉 사실 입력에서 encounter가 만들어지는 과정을 검증한다. 둘 다 남긴다.

## 첫 메시지를 잃지 않는다

발행자를 만든 직후에 발행하면 DDS 매칭이 끝나지 않아 메시지가 사라진다.
S15P11A301-123에서 `CONFIRMED`가 사라지고 `ENDED`만 도착해 녹화가 조용히 아무것도
하지 않았다. `get_subscription_count()`로 구독자를 기다린다.

## 시나리오

    normal      확정 → 안전 정지 → 대화 종료 → 사후 3초 → 보고 완료
    lost        확정 → 사람 상실 → 사후 3초
    redetect    확정 → 대화 종료 → 사후 3초 안에 재감지
    group       사람 3명이 encounter 하나를 공유하는지 (32-6)
    out-of-order 정지 신호가 확정보다 먼저 와도 깨지지 않는지
    approach-failed 접근 실패 시 현재 위치에서 상호작용으로 가는지 (30.3)
    estop       어느 상태에서든 latch되는지 (26.5)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from std_msgs.msg import String

CANDIDATES_TOPIC = '/perception/person_candidates'
SIGNAL_TOPIC = '/mission/signal'


def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec='milliseconds')
        .replace('+00:00', 'Z')
    )


class InputSimulator(Node):
    def __init__(self) -> None:
        super().__init__('simulate_mission_inputs')
        best_effort = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=5,
        )
        reliable = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.candidates_pub = self.create_publisher(
            String, CANDIDATES_TOPIC, best_effort
        )
        self.signal_pub = self.create_publisher(String, SIGNAL_TOPIC, reliable)

    def wait_for_subscribers(self, timeout: float = 5.0) -> bool:
        """두 토픽 모두 구독자가 붙을 때까지 기다린다."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if (
                self.candidates_pub.get_subscription_count() > 0
                and self.signal_pub.get_subscription_count() > 0
            ):
                # 매칭 직후에도 첫 메시지가 드물게 사라진다. 조금 더 기다린다.
                time.sleep(0.3)
                return True
        return False

    def send_candidates(self, track_ids: list[int], confidence: float = 0.86) -> None:
        body = {
            'observedAt': now_utc(),
            'candidates': [
                {
                    'trackId': track,
                    'confidence': confidence,
                    'box': {'x': 100.0 + track * 60, 'y': 200.0, 'width': 80.0, 'height': 200.0},
                    'position': None,
                }
                for track in track_ids
            ],
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.candidates_pub.publish(message)
        label = f'후보 {track_ids}' if track_ids else '후보 없음(빈 배열)'
        print(f'  → {CANDIDATES_TOPIC}  {label}')

    def send_signal(
        self,
        signal: str,
        *,
        source: str = 'TEST',
        encounter_id: str | None = None,
        detail: str = '',
    ) -> None:
        body = {
            'signal': signal,
            'sentAt': now_utc(),
            'source': source,
            'encounterId': encounter_id,
            'detail': detail or None,
            'commandId': None,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.signal_pub.publish(message)
        print(f'  → {SIGNAL_TOPIC}  {signal}')

    def hold(self, seconds: float, *, candidates: list[int] | None = None) -> None:
        """일정 시간 스핀한다. `candidates`를 주면 그동안 계속 발행한다.

        후보를 계속 보내는 것이 실제 탐지 노드의 동작이다. 한 번만 보내면
        `person_lost_seconds`가 지나 상실로 판정된다.
        """
        deadline = time.monotonic() + seconds
        next_publish = 0.0
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.05)
            if candidates is not None and time.monotonic() >= next_publish:
                self.send_candidates(candidates)
                next_publish = time.monotonic() + 0.5


def scenario_normal(sim: InputSimulator) -> None:
    print('시나리오 normal: 확정 → 안전 정지 → 대화 종료 → 사후 3초 → 보고 완료')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[7])
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(3.0, candidates=[7])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    # 사후 3초. 사람이 아직 보이지만 POST_RECORDING에서는 상실이 정상이므로
    # 후보를 보내면 REDETECTED가 된다. 여기서는 보내지 않는다.
    sim.hold(4.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)


def scenario_lost(sim: InputSimulator) -> None:
    print('시나리오 lost: 확정 → 사람 상실 → 사후 3초')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[11])
    print('  (후보 발행을 멈춘다. 빈 배열로 상실을 알린다)')
    sim.send_candidates([])
    sim.hold(4.5, candidates=[])
    sim.hold(4.0)


def scenario_redetect(sim: InputSimulator) -> None:
    print('시나리오 redetect: 확정 → 대화 종료 → 사후 3초 안에 재감지')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[3])
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(1.5, candidates=[3])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(1.0)
    print('  (사후 3초 안에 다시 보인다)')
    sim.hold(2.0, candidates=[3])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(4.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)


def scenario_group(sim: InputSimulator) -> None:
    print('시나리오 group: 사람 3명이 encounter 하나를 공유하는지 (32-6)')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(1.5, candidates=[21])
    print('  (사람이 둘 더 보인다)')
    sim.hold(2.0, candidates=[21, 22, 23])
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(2.0, candidates=[21, 22, 23])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(4.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)


def scenario_out_of_order(sim: InputSimulator) -> None:
    print('시나리오 out-of-order: 신호가 순서를 어겨 와도 깨지지 않는지')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    print('  (사람을 보기 전에 정지·대화 종료 신호가 온다. 둘 다 무시돼야 한다)')
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(0.5)
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(0.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)
    print('  (이제 정상 순서로 진행한다)')
    sim.hold(2.0, candidates=[31])
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(1.5, candidates=[31])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(4.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)


def scenario_approach_failed(sim: InputSimulator) -> None:
    print('시나리오 approach-failed: 접근 실패 시 현재 위치에서 상호작용 (30.3)')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[41])
    sim.send_signal(
        'APPROACH_FAILED', source='NAVIGATION', detail='costmap에 자유 공간이 없다'
    )
    sim.hold(2.0, candidates=[41])
    sim.send_signal('DIALOGUE_ENDED', source='VOICE')
    sim.hold(4.5)
    sim.send_signal('REPORT_COMMITTED', source='VOICE')
    sim.hold(1.0)


def scenario_estop(sim: InputSimulator) -> None:
    print('시나리오 estop: 상호작용 중 E-Stop이 latch되는지 (26.5)')
    sim.send_signal('MISSION_START', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[51])
    sim.send_signal('SAFE_POSE_REACHED', source='NAVIGATION')
    sim.hold(1.5, candidates=[51])
    sim.send_signal('ESTOP', source='SAFETY', detail='물리 버튼')
    sim.hold(1.0)
    print('  (latch 확인: 재개 신호가 통하지 않아야 한다)')
    sim.send_signal('RESUME_APPROVED', source='OPERATOR')
    sim.hold(1.0)
    sim.hold(2.0, candidates=[51])


SCENARIOS = {
    'normal': scenario_normal,
    'lost': scenario_lost,
    'redetect': scenario_redetect,
    'group': scenario_group,
    'out-of-order': scenario_out_of_order,
    'approach-failed': scenario_approach_failed,
    'estop': scenario_estop,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='mission_manager_node 입력을 모사한다 (S15P11A301-133)'
    )
    parser.add_argument(
        '--scenario', choices=sorted(SCENARIOS), default='normal'
    )
    parsed, ros_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    rclpy.init(args=ros_args)
    simulator = InputSimulator()
    try:
        if not simulator.wait_for_subscribers():
            print(
                'mission_manager가 두 토픽을 구독하지 않는다. 노드가 떠 있는지 '
                '확인한다. 지금 발행하면 메시지가 사라진다.',
                file=sys.stderr,
            )
            return 1
        SCENARIOS[parsed.scenario](simulator)
        print('완료. mission_manager 로그와 /mission/status를 확인한다.')
    finally:
        simulator.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())

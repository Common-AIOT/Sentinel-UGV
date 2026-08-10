#!/usr/bin/env python3
"""encounter 신호를 손으로 발행한다 (S15P11A301-123 검증 도구).

AI 탐지 스택을 띄우지 않고 녹화 경로만 독립 검증할 때 쓴다. 실제 노드와 같은
계약(`common/schemas/encounter.schema.json`)으로 신호를 만들며 운영 실행 경로에는
포함되지 않는 개발용 도구다.

## 쓰는 법

VID-03 사전 3초 확인. 확정만 보내고 3초 뒤 종료한다.

    ros2 run sentinel_recorder trigger_encounter --scenario short

VID-04 60초 상호작용. 확정, 접근, 60초 대기, 종료.

    ros2 run sentinel_recorder trigger_encounter --scenario interaction --seconds 60

VID-05 사람 3명. 같은 encounterId로 확정을 세 번 보낸다. encounter 1개와 MP4
1개만 나와야 한다.

    ros2 run sentinel_recorder trigger_encounter --scenario multi-person

낱개 신호를 보낼 때는 phase를 직접 지정한다.

    ros2 run sentinel_recorder trigger_encounter --phase CONFIRMED
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

TOPIC = '/perception/encounter'


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec='milliseconds')
        .replace('+00:00', 'Z')
    )


def envelope(encounter_id: str, phase: str, person_count: int, sequence: int) -> str:
    """31-5 봉투에 담아 보낸다.

    녹화 노드는 봉투가 있어도 없어도 받지만, 봉투 형태로 보내야 AI가 같은
    메시지를 MQTT events로 그대로 흘릴 수 있다.
    """
    return json.dumps(
        {
            'schemaVersion': '1.0',
            'messageId': str(uuid.uuid4()),
            'messageType': 'ENCOUNTER_CONFIRMED',
            'robotId': 'SENTINEL-01',
            'missionId': None,
            'sequence': sequence,
            'sentAt': utc_now(),
            'data': {
                'encounterId': encounter_id,
                'phase': phase,
                'detectedAt': utc_now(),
                'personCount': person_count,
                'trackIds': list(range(1, person_count + 1)) or None,
                'confidence': 0.87,
                'pose': None,
                'missionId': None,
            },
        },
        ensure_ascii=False,
    )


class Trigger(Node):
    def __init__(self) -> None:
        super().__init__('trigger_encounter')
        self.publisher = self.create_publisher(String, TOPIC, 10)
        self.sequence = 0

    def send(self, encounter_id: str, phase: str, person_count: int = 1) -> None:
        self.sequence += 1
        message = String()
        message.data = envelope(encounter_id, phase, person_count, self.sequence)
        self.publisher.publish(message)
        self.get_logger().info(f'{phase} 발행 (encounter={encounter_id[:8]})')
        # 구독자가 받을 시간을 준다. 바로 종료하면 마지막 메시지가 유실된다.
        for _ in range(5):
            rclpy.spin_once(self, timeout_sec=0.02)


def wait(node: Trigger, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)


def wait_for_subscriber(node: Trigger, timeout: float = 10.0) -> bool:
    """구독자가 붙을 때까지 기다린다.

    이것이 없으면 **첫 메시지가 유실된다.** 발행자를 만든 직후에는 DDS 매칭이
    끝나지 않아 발행이 아무에게도 가지 않는다. 고정 시간을 기다리는 방식은
    부하에 따라 실패한다. 실제로 1초를 기다렸는데 CONFIRMED가 유실되고 3초 뒤의
    ENDED만 도착해서, 녹화 노드가 "진행 중 이벤트가 없다"고 무시했다.

    `ros2 topic pub`이 같은 문제를 겪지 않는 이유가 이것이다. 그쪽도 매칭을
    기다린다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if node.publisher.get_subscription_count() > 0:
            return True
        rclpy.spin_once(node, timeout_sec=0.05)
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--scenario',
        choices=['short', 'interaction', 'multi-person', 'lost', 'none'],
        default='none',
    )
    parser.add_argument('--phase', default=None)
    parser.add_argument('--seconds', type=float, default=10.0)
    parser.add_argument('--persons', type=int, default=3)
    parser.add_argument('--encounter-id', default=None)
    # ros2 run이 붙이는 --ros-args를 무시한다.
    known, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    rclpy.init()
    node = Trigger()
    encounter_id = known.encounter_id or str(uuid.uuid4())

    if not wait_for_subscriber(node):
        node.get_logger().error(
            f'{TOPIC} 구독자가 없다. recording_manager가 떠 있는지 확인한다. '
            '구독자 없이 발행하면 메시지가 유실된다.'
        )
        node.destroy_node()
        rclpy.shutdown()
        return 1
    node.get_logger().info(
        f'구독자 {node.publisher.get_subscription_count()}개 확인. 발행을 시작한다.'
    )

    try:
        if known.phase:
            node.send(encounter_id, known.phase.upper())
        elif known.scenario == 'short':
            # VID-03. 사전 3초가 들어갔는지 본다.
            node.send(encounter_id, 'CONFIRMED')
            wait(node, 3.0)
            node.send(encounter_id, 'ENDED')
        elif known.scenario == 'interaction':
            # VID-04. 접근과 대화, 사후 3초가 한 파일로 나오는지 본다.
            node.send(encounter_id, 'CONFIRMED')
            wait(node, 2.0)
            node.send(encounter_id, 'APPROACHED')
            # 30초 무변화면 NO_RESPONSE_TIMEOUT이므로 20초마다 활동을 준다.
            remaining = known.seconds
            while remaining > 0:
                step = min(20.0, remaining)
                wait(node, step)
                remaining -= step
                if remaining > 0:
                    node.send(encounter_id, 'CONFIRMED')
            node.send(encounter_id, 'ENDED')
        elif known.scenario == 'multi-person':
            # VID-05. 같은 encounterId로 여러 번 확정해도 이벤트가 하나여야 한다.
            for index in range(1, known.persons + 1):
                node.send(encounter_id, 'CONFIRMED', person_count=index)
                wait(node, 0.5)
            wait(node, 2.0)
            node.send(encounter_id, 'ENDED')
        elif known.scenario == 'lost':
            node.send(encounter_id, 'CONFIRMED')
            wait(node, 2.0)
            node.send(encounter_id, 'LOST')
        else:
            node.get_logger().info('--scenario 또는 --phase를 지정한다')

        # 녹화 노드가 사후 3초를 세고 마무리할 시간을 준다.
        wait(node, float(known.seconds if known.phase else 6.0))
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

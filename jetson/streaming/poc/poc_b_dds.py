#!/usr/bin/env python3
"""PoC-B 2/3 — 압축 토픽 DDS 전송 경로 측정 (S15P11A301-62).

합격 조건 2·4를 판정한다.

* 조건 2: 구독자 2개 모두 프레임 드롭 0
* 조건 4: JPEG 최대 크기가 DDS 단편화 손실을 유발하지 않음

CompressedImage에는 시퀀스 번호가 없으므로 드롭은 `header.stamp` 간격으로
추정한다. 카메라가 V4L2 캡처 시각을 stamp에 넣기 때문에(S15P11A301-62 계약)
프레임이 유실되면 연속 stamp 간격이 프레임 주기의 배수로 벌어진다.

사용법:
    ./poc_b_dds.py --label sub_a --seconds 60 --out /tmp/poc_b/dds_a.json

두 인스턴스를 동시에 띄워야 조건 2를 판정할 수 있다. poc_b_dds.sh가 그 역할을 한다.
"""

import argparse
import json
import statistics
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage

TOPIC = '/camera/image_raw/compressed'


class CompressedImageProbe(Node):
    def __init__(self, label: str, expected_fps: float, depth: int):
        super().__init__(f'poc_b_probe_{label}')
        self.label = label
        self.expected_period = 1.0 / expected_fps
        self.sizes: list[int] = []
        self.stamps: list[float] = []
        self.qos_note = 'unknown'

        qos = self._match_publisher_qos(depth)
        self.create_subscription(CompressedImage, TOPIC, self._on_image, qos)

    def _match_publisher_qos(self, depth: int, discovery_timeout: float = 5.0) -> QoSProfile:
        """발행자 QoS를 조회해 맞춘다. 불일치하면 연결 자체가 안 되거나
        의도치 않은 드롭이 생겨 측정이 무의미해진다.

        DDS 발견에는 시간이 걸리므로 조회 전에 기다린다. 생성 직후 바로 조회하면
        항상 '미발견'이 되어 QoS를 잘못 가정한다."""
        deadline = self.get_clock().now().nanoseconds + int(discovery_timeout * 1e9)
        infos = []
        while self.get_clock().now().nanoseconds < deadline:
            infos = self.get_publishers_info_by_topic(TOPIC)
            if infos:
                break
            rclpy.spin_once(self, timeout_sec=0.2)

        reliability = ReliabilityPolicy.RELIABLE
        if infos:
            pub_qos = infos[0].qos_profile
            reliability = pub_qos.reliability
            self.qos_note = (
                f'publisher reliability={reliability.name}, '
                f'durability={pub_qos.durability.name} (발견됨, 일치시킴)'
            )
        else:
            self.qos_note = (
                f'publisher를 {discovery_timeout}초 내 발견하지 못함 — '
                'RELIABLE로 가정. 수신이 0이면 QoS 불일치를 의심한다'
            )

        return QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=reliability,
            durability=DurabilityPolicy.VOLATILE,
        )

    def _on_image(self, msg: CompressedImage) -> None:
        self.sizes.append(len(msg.data))
        self.stamps.append(msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9)

    def report(self) -> dict:
        if len(self.stamps) < 2:
            return {
                'label': self.label,
                'received': len(self.stamps),
                'error': '수신 메시지가 2개 미만 — 발행자·QoS를 확인한다',
                'qos': self.qos_note,
            }

        deltas = [b - a for a, b in zip(self.stamps, self.stamps[1:])]
        # 프레임 주기의 1.5배를 넘으면 그 사이에 유실이 있었다고 본다.
        gap_threshold = self.expected_period * 1.5
        gaps = [d for d in deltas if d > gap_threshold]
        dropped = sum(round(d / self.expected_period) - 1 for d in gaps)

        span = self.stamps[-1] - self.stamps[0]
        return {
            'label': self.label,
            'qos': self.qos_note,
            'received': len(self.stamps),
            'span_seconds': round(span, 3),
            'measured_fps': round(len(self.stamps) / span, 3) if span > 0 else None,
            'gap_events': len(gaps),
            'estimated_dropped_frames': int(dropped),
            'max_gap_seconds': round(max(deltas), 4),
            'jpeg_size_bytes': {
                'mean': int(statistics.fmean(self.sizes)),
                'min': min(self.sizes),
                'max': max(self.sizes),
                # Fast DDS 기본 UDP 최대 페이로드(약 64KB)를 넘으면 단편화된다.
                'over_64kb_count': sum(1 for s in self.sizes if s > 65_507),
            },
        }


def summarize(paths: list[str]) -> int:
    """저장된 결과 JSON을 사람이 읽는 형태로 출력한다."""
    status = 0
    for path in paths:
        try:
            with open(path, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f'{path}: 읽을 수 없음 - {exc}')
            status = 1
            continue

        label = data.get('label', path)
        if 'error' in data:
            print(f'{label:>10}: 오류 - {data["error"]}')
            status = 1
            continue

        size = data['jpeg_size_bytes']
        print(
            f'{label:>10}: {data["received"]}프레임 {data["measured_fps"]}fps  '
            f'드롭추정 {data["estimated_dropped_frames"]} '
            f'(gap {data["gap_events"]}회, 최대 {data["max_gap_seconds"]}s)'
        )
        print(
            f'{"":>10}  JPEG 평균 {size["mean"]}B / 최대 {size["max"]}B, '
            f'64KB 초과 {size["over_64kb_count"]}프레임'
        )
        print(f'{"":>10}  QoS: {data["qos"]}')
        if data['estimated_dropped_frames'] > 0:
            status = 1
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--label')
    parser.add_argument('--seconds', type=float, default=60.0)
    parser.add_argument('--fps', type=float, default=30.0)
    parser.add_argument('--depth', type=int, default=10)
    parser.add_argument('--out')
    parser.add_argument(
        '--summarize', nargs='+', metavar='JSON',
        help='측정 대신 저장된 결과 JSON을 요약 출력한다')
    args = parser.parse_args()

    if args.summarize:
        return summarize(args.summarize)

    if not args.label:
        parser.error('--label은 측정 모드에서 필수다')

    rclpy.init()
    node = CompressedImageProbe(args.label, args.fps, args.depth)
    end = node.get_clock().now().nanoseconds + int(args.seconds * 1e9)
    try:
        while rclpy.ok() and node.get_clock().now().nanoseconds < end:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass

    result = node.report()
    node.destroy_node()
    rclpy.shutdown()

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as handle:
            handle.write(text + '\n')
    return 0 if 'error' not in result else 1


if __name__ == '__main__':
    sys.exit(main())

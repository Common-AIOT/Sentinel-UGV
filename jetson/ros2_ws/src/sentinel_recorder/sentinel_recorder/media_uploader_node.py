#!/usr/bin/env python3
"""이벤트 미디어 업로드 노드 (S15P11A301-124, 명세 31-7).

`recording_manager`가 `pending/`에 남긴 이벤트를 백엔드 Presigned URL로 올린다.

## 왜 별 노드인가

`recording_manager`와 같은 프로세스에 두면 업로드가 녹화를 막는다. 5분 영상은 약
94MB이고 Wi-Fi가 느리면 수십 초가 걸린다. 그 사이에 사람이 또 발견되면 조각을
모으지 못한다.

`#123`이 스트리밍과 녹화를 나눈 것과 같은 이유다. 이 노드를 `kill -9`해도 녹화는
계속되고 `pending/`에 쌓인다. 다시 뜨면 그것부터 올린다.

## 업로드가 스트리밍을 방해하지 않게

한 주기에 올리는 이벤트 수를 제한한다. 망이 살아난 직후 수십 건을 한꺼번에 올리면
Wi-Fi를 점유해 WebRTC 지연이 튄다. 관제 영상이 우선이다(32장 우선순위).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from .pending_store import PendingStore
from .upload_client import UploadClient
from .upload_worker import UploadWorker


class MediaUploaderNode(Node):
    def __init__(self) -> None:
        super().__init__('media_uploader')

        self.declare_parameter(
            'pending_directory', '/var/lib/sentinel/media/pending'
        )
        # 백엔드 주소. EC2에 배포된 Spring Boot다. 자격증명을 젯슨에 두지 않고
        # Presigned URL을 받아 쓰는 것이 31-7의 요지다.
        #
        # apex 도메인이 아니라 API 호스트다. sentinel-ugv.xyz는 Vercel 프론트이고
        # 모든 API 요청이 404가 된다 — 프론트가 200을 주므로 망 문제로 보이지도
        # 않는다. demo.launch.py가 이 값을 손으로 덮어쓰던 것을 기본값으로
        # 올렸다(S15P11A301-171에서 지도 업로더가 같은 함정을 밟았다).
        self.declare_parameter(
            'backend_base_url', 'https://api.sentinel-ugv.xyz'
        )
        self.declare_parameter('auth_token', '')
        self.declare_parameter('robot_id', 'SENTINEL-01')
        self.declare_parameter('poll_period_seconds', 10.0)
        self.declare_parameter('max_per_cycle', 2)
        self.declare_parameter('max_pending_seconds', 1800)
        self.declare_parameter('encoder_bitrate_kbps', 2500)
        # 이벤트 영상의 AAC 트랙(S15P11A301-131). 오디오를 끈 구성에서는 0을 준다.
        self.declare_parameter('audio_bitrate_kbps', 64)
        # 개발·장애 진단 시 업로드까지만 검증하고 완료 등록을 건너뛸 수 있다.
        # 운영 기본값은 false다. 켜면 서버가 AVAILABLE을 모르므로 다시보기 목록에
        # 나타나지 않는다.
        self.declare_parameter('skip_complete', False)

        self.pending = PendingStore(
            self._param('pending_directory'),
            int(self._param('max_pending_seconds')),
            int(self._param('encoder_bitrate_kbps')),
            int(self._param('audio_bitrate_kbps')),
        )
        self.client = UploadClient(
            str(self._param('backend_base_url')),
            auth_token=self._param('auth_token') or None,
        )
        self.worker = UploadWorker(
            self.pending,
            self.client,
            robot_id=str(self._param('robot_id')),
            skip_complete=bool(self._param('skip_complete')),
            max_per_cycle=int(self._param('max_per_cycle')),
        )

        self.status_pub = self.create_publisher(String, '~/status', 10)
        self.create_timer(float(self._param('poll_period_seconds')), self._on_tick)

        self.get_logger().info(
            f'media_uploader 시작. pending={self.pending.directory} '
            f'backend={self._param("backend_base_url")} '
            f'주기={self._param("poll_period_seconds")}초 '
            f'skip_complete={self._param("skip_complete")}'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _on_tick(self) -> None:
        stats = self.worker.run_once()
        if stats.attempted == 0 and stats.skipped_permanent == 0:
            return

        for detail in stats.details:
            self.get_logger().info(f'  {detail}')

        if stats.failed:
            self.get_logger().warn(
                f'업로드 시도 {stats.attempted}건 중 {stats.failed}건 실패. '
                '지수 백오프로 재시도한다.'
            )
        if stats.skipped_permanent:
            # 재시도해도 같은 결과인 것들. 계약 불일치나 미구현 엔드포인트다.
            # 조용히 넘기면 왜 업로드가 안 되는지 알 수 없다.
            self.get_logger().warn(
                f'영구 실패로 건너뛴 이벤트 {stats.skipped_permanent}건. '
                '백엔드 계약을 확인한다. 고친 뒤에는 노드를 재시작한다.'
            )

        message = String()
        message.data = json.dumps(
            {
                'attempted': stats.attempted,
                'succeeded': stats.succeeded,
                'failed': stats.failed,
                'skippedPermanent': stats.skipped_permanent,
                'pendingBytes': self.pending.total_bytes(),
                'capBytes': self.pending.cap_bytes,
                'at': datetime.now(timezone.utc)
                .isoformat(timespec='milliseconds')
                .replace('+00:00', 'Z'),
            },
            ensure_ascii=False,
        )
        self.status_pub.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MediaUploaderNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

"""저장된 SLAM 지도를 백엔드에 올린다 (S15P11A301-171 후반부).

    /var/lib/sentinel/maps/<missionId>/map.pgm    slam_toolbox가 저장
                                      /map.yaml
                                      /report.json  uploadState

`map_saver`(전반부)와 별 노드로 둔다. 32장의 장애 격리 원칙이다 - 업로드가
망 때문에 막혀도 지도 저장은 계속돼야 한다. 반대로 업로더가 죽어도 파일은
디스크에 남아 다음 기동에서 이어받는다.

## 왜 폴링인가

저장 완료를 토픽으로 받지 않고 디렉터리를 주기적으로 훑는다. 이벤트 영상
업로더(S15P11A301-125)와 같은 구조이며 이유도 같다 - 프로세스가 죽은 사이에
저장된 지도, 그리고 망이 끊겼던 동안 쌓인 지도를 **재기동만으로** 집어야 한다.
토픽 알림에만 의존하면 그 구간이 영구히 누락된다(31-10).

## mapId

발급 응답의 `mapId`가 13.2 maps 행의 식별자다. 성공 시 report.json에 적고
`~/registered`로 발행한다. telemetry·encounter의 mapId를 이 값으로 통일하는
일은 남아 있다 - 지도는 임무 **종료** 시 등록되는데 그 두 값은 임무 **중**에
필요하므로, 같은 임무 안에서는 시점이 맞지 않는다. 자세한 것은 README에 적었다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from .map_store import MapStore, REPORT_NAME, write_report
from .map_upload import (
    AttemptState,
    backoff_delay,
    failed_report,
    is_due,
    needs_upload,
    registered_report,
    sha256_of,
)
from .map_upload_client import MapUploadClient
from .upload_client import UploadError


class MapUploaderNode(Node):
    def __init__(self) -> None:
        super().__init__('map_uploader')

        self.declare_parameter('map_directory', '/var/lib/sentinel/maps')
        self.declare_parameter('backend_base_url', 'https://sentinel-ugv.xyz')
        self.declare_parameter('auth_token', '')
        self.declare_parameter('poll_period_seconds', 15.0)
        # 한 주기에 올리는 지도 수. 지도는 이벤트 영상보다 작지만(수백 KB)
        # 여러 임무가 쌓였을 때 한꺼번에 올리면 스트리밍 대역을 뺏는다.
        self.declare_parameter('max_per_cycle', 1)
        # 재시도 간격. 32-3이 무한 재시작을 금지하므로 표로 둔다. 지수적으로
        # 늘리지 않는 이유는 현장에서 Wi-Fi가 돌아왔을 때 몇 분씩 기다리면
        # 안 되기 때문이다.
        self.declare_parameter('retry_backoff_seconds', [5.0, 15.0, 60.0, 300.0])
        # 완료 호출을 건너뛴다. 백엔드 없이 PUT 경로만 시험할 때 쓴다.
        self.declare_parameter('skip_complete', False)

        self.store = MapStore(str(self._param('map_directory')))
        self.store.prepare()
        self.client = MapUploadClient(
            str(self._param('backend_base_url')),
            auth_token=str(self._param('auth_token')) or None,
        )
        self._attempts: dict[str, AttemptState] = {}
        self._registered: dict[str, str] = {}

        self.status_pub = self.create_publisher(String, '~/status', 10)
        # 등록된 mapId. 임무당 한 번뿐인 신호라 VOLATILE로 두면 나중에 뜬
        # 구독자가 영영 못 받는다. mission_manager의 status와 같은 이유로
        # TRANSIENT_LOCAL이다 - MQTT Retain에 대응한다(31-4).
        self.registered_pub = self.create_publisher(
            String,
            '~/registered',
            QoSProfile(
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                history=QoSHistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )

        self.create_timer(
            float(self._param('poll_period_seconds')), self._tick
        )
        self.get_logger().info(
            f'map_uploader 시작. 지도={self.store.root} '
            f'백엔드={self.client.base_url}'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """올릴 지도를 찾아 순서대로 처리한다.

        예외가 타이머 밖으로 나가지 않게 한다. rclpy 타이머에서 예외가 나면
        이후 주기가 서지 않고, 그러면 업로드가 조용히 멈춘다.
        """
        try:
            self._run_cycle()
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f'업로드 주기에서 예외: {error}')

    def _run_cycle(self) -> None:
        now = time.monotonic()
        budget = int(self._param('max_per_cycle'))
        for mission_id in self.store.iter_missions():
            if budget <= 0:
                return
            if not self._should_try(mission_id, now):
                continue
            budget -= 1
            self._upload_one(mission_id, now)

    def _should_try(self, mission_id: str, now: float) -> bool:
        saved = self.store.scan(mission_id)
        if saved is None or not saved.complete:
            # pgm만 있고 yaml이 없는 상태다. 저장 중이거나 실패한 것이므로
            # 올리지 않는다 - yaml 없이는 관제가 좌표를 얹을 수 없다.
            return False
        if not needs_upload(self.store.read_report(mission_id)):
            return False
        state = self._attempts.get(mission_id)
        return state is None or is_due(state, now)

    def _upload_one(self, mission_id: str, now: float) -> None:
        directory = self.store.directory_for(mission_id)
        pgm = directory / 'map.pgm'
        yaml_path = directory / 'map.yaml'
        report = self.store.read_report(mission_id) or {}

        try:
            presign = self.client.request_upload(mission_id=mission_id)
            self.client.put_pair(presign, pgm=pgm, yaml_path=yaml_path)
            already = False
            if not bool(self._param('skip_complete')):
                already = self.client.complete(map_id=presign.map_id)
        except UploadError as error:
            self._record_failure(mission_id, error, now, report)
            return

        # 해시는 성공 후에 계산한다. 실패할 경로에서 파일을 두 번 읽지 않는다.
        try:
            pgm_hash = sha256_of(pgm)
            yaml_hash = sha256_of(yaml_path)
        except OSError:
            pgm_hash = yaml_hash = ''

        updated = registered_report(
            report,
            map_id=presign.map_id,
            pgm_key=presign.pgm_key,
            yaml_key=presign.yaml_key,
            uploaded_at=self._now_iso(),
            pgm_sha256=pgm_hash,
            yaml_sha256=yaml_hash,
        )
        write_report(directory / REPORT_NAME, updated)
        self._attempts.pop(mission_id, None)
        self._registered[mission_id] = presign.map_id

        self.get_logger().info(
            f'지도 등록 완료. mission={mission_id} mapId={presign.map_id} '
            f'pgm={presign.pgm_key or "-"}'
            + (' (이미 등록됨)' if already else '')
        )
        self._publish_registered(mission_id, presign.map_id)
        self._publish_status()

    def _record_failure(
        self, mission_id: str, error: UploadError, now: float, report: dict
    ) -> None:
        state = self._attempts.setdefault(mission_id, AttemptState())
        state.failures += 1
        state.last_reason = error.reason
        if not error.retryable:
            state.permanent = True
            self.get_logger().error(
                f'지도 업로드 영구 실패. mission={mission_id} {error}. '
                f'파일은 {self.store.directory_for(mission_id)}에 남는다.'
            )
        else:
            schedule = [float(x) for x in self._param('retry_backoff_seconds')]
            delay = backoff_delay(state.failures, schedule)
            state.next_attempt_at = now + delay
            self.get_logger().warning(
                f'지도 업로드 실패({state.failures}회). mission={mission_id} '
                f'{error}. {delay:.0f}초 후 재시도.'
            )

        # 실패도 파일에 남긴다. 재기동 후 사람이 원인을 볼 수 있어야 한다.
        try:
            write_report(
                self.store.directory_for(mission_id) / REPORT_NAME,
                failed_report(
                    report,
                    reason=str(error),
                    failures=state.failures,
                    permanent=state.permanent,
                ),
            )
        except OSError as write_error:
            self.get_logger().warning(f'실패 기록을 쓰지 못했다: {write_error}')
        self._publish_status()

    # ------------------------------------------------------------------

    def _publish_registered(self, mission_id: str, map_id: str) -> None:
        message = String()
        message.data = json.dumps(
            {'missionId': mission_id, 'mapId': map_id},
            ensure_ascii=False,
        )
        self.registered_pub.publish(message)

    def _publish_status(self) -> None:
        pending = [
            m
            for m in self.store.iter_missions()
            if needs_upload(self.store.read_report(m))
        ]
        message = String()
        message.data = json.dumps(
            {
                'pending': len(pending),
                'registered': len(self._registered),
                'permanentFailures': sum(
                    1 for s in self._attempts.values() if s.permanent
                ),
            },
            ensure_ascii=False,
        )
        self.status_pub.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MapUploaderNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

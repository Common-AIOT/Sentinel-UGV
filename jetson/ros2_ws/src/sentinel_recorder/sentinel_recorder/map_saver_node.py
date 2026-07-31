#!/usr/bin/env python3
"""임무가 끝나면 SLAM 지도를 저장한다 (S15P11A301-171, 명세 13.2·31-10).

`/mission/status`를 구독해 `COMPLETED` 전이를 보면 slam_toolbox의 `save_map`
서비스를 불러 `map.pgm`과 `map.yaml`을 로컬에 남긴다.

    /var/lib/sentinel/maps/<missionId>/map.pgm
                                      /map.yaml
                                      /report.json   업로드 대기 표시

## 업로드는 아직 하지 않는다

지도 업로드 API가 백엔드에 없다(2026-07-30 확인 — Swagger에 maps 엔드포인트
0건). `report.json`의 `uploadState: UPLOAD_PENDING`이 그 경계이고, API가 생기면
이 디렉터리를 훑는 업로더만 붙이면 된다. 저장 코드는 바뀌지 않는다.

31-10이 "업로드 대기 영상·지도"를 로컬 보존 대상으로 정했으므로, 업로드가 없어도
저장 자체가 요구사항이다. 망이 끊긴 채 임무가 끝나도 지도를 잃지 않는다.

## 왜 별 노드인가

지도 저장은 수 초가 걸리고 실패할 수 있다. `mission_manager` 안에서 하면 그 동안
상태 머신이 멈추고, 저장 실패가 임무 상태에 영향을 준다. 32장 장애 격리 원칙대로
프로세스를 나누고 토픽으로만 만난다.

`sentinel_recorder`에 두는 이유는 이 패키지가 이미 "산출물을 로컬에 남기고 나중에
올린다"를 담당하기 때문이다(이벤트 MP4). 나중에 업로더를 합칠 때 같은 자리에 있는
편이 낫다.

## 재시도가 필요한 이유

`save_map`은 slam_toolbox 안의 lifecycle map_saver가 처리하고, 그것은 `/map`을
**2초만 구독하고 기다린다**(nav2 map_io 기본값). 우리 `/map`은
`map_update_interval: 2.0`이라 2초 주기로 발행되므로 두 값이 정확히 맞물려
경합한다. 실측에서 한 번은 잡히고 한 번은 놓쳤다.

    [map_saver]: Failed to spin map subscription     ← 놓친 경우, result=255

그래서 실패하면 잠깐 뒤 다시 부른다. 발행 주기를 한 번 이상 건너뛰면 잡힌다.
`map_update_interval`을 낮추는 방법도 있지만 그쪽은 SLAM의 CPU를 상시로 더 쓰고,
저장은 임무당 한 번뿐이라 재시도가 값이 싸다.

## COMPLETED 시점에 SLAM이 아직 살아 있어야 한다

`save_map`은 slam_toolbox가 응답하는 서비스다. 임무 종료로 SLAM을 내리는 코드는
없고(launch 종료 때 함께 내려간다) 이 노드가 COMPLETED 직후에 부르므로 문제가
없다. 만약 나중에 임무 종료 시 SLAM을 내리게 바뀌면 이 순서가 깨진다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from slam_toolbox.srv import SaveMap
from std_msgs.msg import String

from .map_store import (
    PGM_NAME,
    YAML_NAME,
    REPORT_NAME,
    MapStore,
    read_map_yaml,
    write_report,
)

STATE_COMPLETED = 'COMPLETED'


class MapSaverNode(Node):
    def __init__(self) -> None:
        super().__init__('map_saver')

        self.declare_parameter('mission_status_topic', '/mission/status')
        self.declare_parameter('map_directory', '/var/lib/sentinel/maps')
        self.declare_parameter('save_map_service', '/slam_toolbox/save_map')
        # 저장 재시도. slam_toolbox 내부 map_saver가 /map을 2초만 기다리고
        # 우리 /map은 2초 주기라 경합한다(모듈 문서 참고). 실패하면 발행 주기를
        # 건너뛸 만큼 쉬고 다시 부른다.
        self.declare_parameter('save_retry_limit', 4)
        self.declare_parameter('save_retry_delay_seconds', 2.5)

        self.store = MapStore(self._param('map_directory'))
        try:
            self.store.prepare()
        except OSError as error:
            self.get_logger().error(
                f'지도 디렉터리를 만들 수 없다({error}). 지도를 저장할 수 없다.'
            )

        # 이 프로세스에서 이미 저장한 missionId.
        #
        # /mission/status는 heartbeat 없이 전이 때만 오지만, TRANSIENT_LOCAL이라
        # 늦게 뜬 구독자가 마지막 값을 즉시 받는다. 그 값이 이미 COMPLETED면
        # 재기동마다 같은 임무의 지도를 다시 저장한다 — 그때 SLAM은 새 지도를
        # 만들고 있으므로 이전 임무 디렉터리에 엉뚱한 지도를 덮어쓴다.
        self._saved_missions: set[str] = set()
        # 진행 중인 저장. 서비스 응답을 기다리는 동안 같은 전이가 또 오면 무시한다.
        self._in_flight: str | None = None
        # 재시도 타이머. 일회성이므로 발화 후 스스로 정리한다.
        self._retry_timer = None
        self._attempts = 0

        # mission_manager가 TRANSIENT_LOCAL로 발행한다. VOLATILE로 구독하면
        # 이 노드가 나중에 떠서 COMPLETED 전이를 놓친다 — 임무당 한 번뿐인
        # 신호라 놓치면 그 임무의 지도가 영영 저장되지 않는다.
        self.create_subscription(
            String,
            self._param('mission_status_topic'),
            self._on_status,
            QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            ),
        )

        self.save_client = self.create_client(
            SaveMap, str(self._param('save_map_service'))
        )

        self.get_logger().info(
            f'map_saver 시작. 지도={self.store.root} '
            f'서비스={self._param("save_map_service")} '
            f'상태={self._param("mission_status_topic")}'
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    @staticmethod
    def _now_iso() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec='milliseconds')
            .replace('+00:00', 'Z')
        )

    # ------------------------------------------------------------------
    # 임무 상태
    # ------------------------------------------------------------------

    def _on_status(self, message: String) -> None:
        try:
            body = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'임무 상태 JSON 해석 실패: {error}')
            return
        if not isinstance(body, dict):
            return
        if body.get('state') != STATE_COMPLETED:
            return

        mission_id = body.get('missionId')
        mission_id = mission_id if isinstance(mission_id, str) else None
        key = mission_id or ''

        if key in self._saved_missions:
            # 재기동 시 TRANSIENT_LOCAL이 주는 옛 COMPLETED다. 다시 저장하면
            # 이전 임무 디렉터리에 새 지도를 덮어쓴다.
            return
        if self._in_flight is not None:
            self.get_logger().info(
                f'지도 저장이 진행 중이다({self._in_flight[:8] or "no-mission"}). '
                '이번 전이는 건너뛴다.'
            )
            return

        self._save(mission_id)

    # ------------------------------------------------------------------
    # 저장
    # ------------------------------------------------------------------

    def _save(self, mission_id: str | None) -> None:
        label = (mission_id or 'no-mission')[:8]

        if not self.save_client.service_is_ready():
            # SLAM이 없으면 저장할 지도가 없다. 값을 지어내지 않고 사실만 남긴다.
            self.get_logger().warn(
                f'{label}: {self._param("save_map_service")} 가 없다. '
                'SLAM이 떠 있지 않으면 저장할 지도가 없다.'
            )
            return

        directory = self.store.directory_for(mission_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.get_logger().error(f'{label}: 디렉터리 생성 실패({error})')
            return

        request = SaveMap.Request()
        # slam_toolbox가 여기에 .pgm/.yaml을 붙인다. 절대경로여야 한다 —
        # 상대경로면 launch로 띄운 노드의 작업 디렉터리에 쓰인다.
        request.name = String(data=str(self.store.basename_for(mission_id)))

        self._in_flight = mission_id or ''
        self._attempts += 1
        future = self.save_client.call_async(request)
        future.add_done_callback(
            lambda done, mid=mission_id: self._on_saved(done, mid)
        )
        self.get_logger().info(
            f'{label}: 지도 저장을 요청했다 ({self._attempts}회) → {directory}'
        )

    def _schedule_retry(self, mission_id: str | None, delay: float) -> None:
        """일회성 타이머로 다시 부른다.

        `time.sleep`을 쓰면 안 된다. 이 노드는 단일 스레드 실행기에서 돌고,
        여기서 자면 상태 구독까지 멈춘다.
        """
        def fire() -> None:
            if self._retry_timer is not None:
                self._retry_timer.cancel()
                self.destroy_timer(self._retry_timer)
                self._retry_timer = None
            self._save(mission_id)

        self._retry_timer = self.create_timer(delay, fire)

    def _on_saved(self, future, mission_id: str | None) -> None:
        """서비스 응답. **콜백 안에서 예외를 흘리면 안 된다.**

        rclpy의 future 콜백에서 나간 예외는 조용히 삼켜지거나 실행기를 흔든다.
        여기서 실패해도 다음 임무의 저장이 막히지 않도록 `_in_flight`를 반드시
        비운다.
        """
        label = (mission_id or 'no-mission')[:8]
        self._in_flight = None
        try:
            result = future.result()
        except Exception as error:  # noqa: BLE001 - 실행기를 지켜야 한다
            self.get_logger().error(f'{label}: 저장 서비스 호출 실패({error!r})')
            return

        # slam_toolbox의 SaveMap 응답은 result 코드다. 0이 성공이다. 다만
        # 코드만 믿지 않고 파일을 직접 확인한다 — 성공 코드가 왔는데 파일이
        # 없는 경우를 이벤트 MP4에서 겪었다(S15P11A301-131의 빈 썸네일).
        code = getattr(result, 'result', None)
        saved = self.store.scan(mission_id)
        if saved is None or not saved.complete:
            limit = int(self._param('save_retry_limit'))
            if self._attempts < limit:
                delay = float(self._param('save_retry_delay_seconds'))
                self.get_logger().warn(
                    f'{label}: 저장 실패(result={code}). {delay}초 뒤 다시 '
                    f'시도한다 ({self._attempts}/{limit}). '
                    '내부 map_saver가 /map을 놓친 경우다.'
                )
                self._schedule_retry(mission_id, delay)
                return
            self.get_logger().error(
                f'{label}: {limit}회 시도했으나 저장하지 못했다(result={code}). '
                f'{PGM_NAME}/{YAML_NAME}를 확인한다. SLAM이 /map을 내고 있는지 본다.'
            )
            self._attempts = 0
            return

        resolution, origin = read_map_yaml(saved.directory / YAML_NAME)
        if resolution is None:
            self.get_logger().warn(
                f'{label}: {YAML_NAME}에서 해상도를 읽지 못했다. '
                '형식이 바뀌었는지 확인한다.'
            )

        report = self.store.build_report(
            mission_id=mission_id,
            saved=saved,
            saved_at=self._now_iso(),
            resolution=resolution,
            origin=origin,
        )
        try:
            write_report(saved.directory / REPORT_NAME, report)
        except OSError as error:
            self.get_logger().error(f'{label}: 보고서 쓰기 실패({error})')
            return

        self._saved_missions.add(mission_id or '')
        self._attempts = 0
        self.get_logger().info(
            f'{label}: 지도 저장 완료 '
            f'pgm {saved.pgm_bytes / 1e3:.0f}KB, yaml {saved.yaml_bytes}B, '
            f'해상도 {resolution}. 업로드 대기(API 미구현, S15P11A301-171).'
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MapSaverNode()
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

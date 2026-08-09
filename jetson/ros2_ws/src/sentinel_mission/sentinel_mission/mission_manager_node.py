#!/usr/bin/env python3
"""Mission Manager 노드 (S15P11A301-133, 명세 26장).

`/perception/encounter`를 발행하는 **유일한** 노드다. 26.1의 단일 권한 원칙이다.

## 왜 발행자를 하나로 모으는가

이 토픽에 여러 노드가 발행하면 세 가지가 깨진다.

`encounterId`를 누가 발급할지 정해지지 않으므로 탐지·음성·주행이 서로 다른 UUID를
쓴다. 녹화 노드는 `encounterId`로 같은 이벤트인지 판단하므로(32-6) 한 사람에 대한
이벤트가 둘·셋으로 쪼개진다.

`CONFIRMED` 없이 `ENDED`가 먼저 도착할 수 있다. 녹화 상태 머신은 진행 중 이벤트가
없는 신호를 무시하므로 대화 구간이 통째로 빠진 영상이 나온다. S15P11A301-123
검증에서 실제로 그렇게 됐고, 그때는 발행 타이밍 문제였지만 발행자가 여럿이면
상시 상태가 된다.

`LOST`와 `REDETECTED`는 사후 3초 창 안에서만 의미가 있다. 서로 모르는 노드가
발행하면 그 창을 지킬 수 없다.

## 입력은 사실만 받는다

    /perception/person_candidates       탐지 노드가 확정한 사람 후보 (25.2)
    /mission/signal                     주행·음성·관제·안전이 알리는 사건 (26.1)
    /esp32_motor_bridge/drive_state     모터 보드의 현재 상태 (S15P11A301-298)
    /esp32_motor_bridge/command_ack     모터 보드의 명령 응답 (S15P11A301-298)

보내는 쪽은 "무슨 일이 있었는지"만 적고 "어떤 상태로 가야 하는지"는 적지 않는다.
그 판단이 이 노드에 있어야 26.1이 성립한다.

## 왜 이 노드가 모터 보드를 직접 보는가 (S15P11A301-298)

수동/자율 모드에서 **바퀴 소유자를 정하는 것은 모터 ESP32**다. 폰이 자기 핫스팟
위에서 그 보드에 직결하므로 젯슨은 조종 패킷을 볼 수도 막을 수도 없다. 즉
`DRIVE_STATE.state == MANUAL_ACTIVE` 는 이 노드가 **따라가야 할 사실**이고,
26.1 이 이 노드를 「사실 → 전이」 변환기로 정의했으므로 관측자도 여기다.

50Hz 사실을 디바운스해 신호로 바꾸는 것은 `/perception/person_candidates` 와 같은
구조이고, 판정은 전부 `mode_gateway`(순수 모듈)가 한다. 별도 `mode_arbiter` 노드를
두지 않은 이유는 `CommandRelay`·`cloud_bridge` 가 두 번째 라우팅 대상·생존 확인·
launch 항목·새 실패 모드를 배워야 하기 때문이다 — 기존 경로는 dict 항목 두 개로
끝난다.

## 출력

    /perception/encounter           녹화 트리거 (32-5). 발행자는 이 노드뿐이다
    /mission/status                 임무 상태 (26.2). 주행 노드가 movementAllowed를 본다
    /esp32_motor_bridge/set_mode    모드 전환 요청 (S15P11A301-298). 브리지가 중계만 한다

## 이 노드가 죽으면

녹화 노드는 진행 중 이벤트를 자기 타임아웃으로 마감한다(32-5의
NO_RESPONSE_TIMEOUT, MAX_EVENT). 조각은 이미 모았으므로 영상은 남는다. 주행 노드는
`/mission/status`가 끊기면 마지막 상태를 유지하지 말고 정지해야 하는데, 그것은
주행 노드 쪽 책임이며 이 티켓 범위가 아니다.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import rclpy
import tf2_ros
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from .geometry import encounter_pose
from .mission_state import (
    ACK_EXECUTED,
    ACK_REJECTED,
    UNIMPLEMENTED,
    MissionState,
    MissionStateMachine,
    Phase,
    Signal,
    format_utc,
)
from .mode_gateway import MODE_AUTO, MODE_MANUAL, ModeGateway
from .slam_reset import reset_slam_process

SCHEMA_VERSION = '1.0'

# `MANUAL_REQUESTED`/`AUTO_REQUESTED` 는 상태기계에 닿기 전에 여기서 가로챈다.
# 그 둘은 **의도**이고, 상태기계는 **사실**(`*_ENGAGED`)만 다룬다.
_MODE_REQUEST_SIGNALS = {
    Signal.MANUAL_REQUESTED: MODE_MANUAL,
    Signal.AUTO_REQUESTED: MODE_AUTO,
}


class MissionManagerNode(Node):
    def __init__(self) -> None:
        super().__init__('mission_manager')

        self.declare_parameter('candidates_topic', '/perception/person_candidates')
        self.declare_parameter('signal_topic', '/mission/signal')
        self.declare_parameter('encounter_topic', '/perception/encounter')
        self.declare_parameter('status_topic', '/mission/status')
        # 관제 명령 처리 결과 (S15P11A301-143)
        self.declare_parameter(
            'command_result_topic', '/mission/command_result'
        )
        self.declare_parameter('post_recording_seconds', 3)
        # 임무 시작마다 slam 을 재시작해 지도를 초기화한다 (S15P11A301-362).
        # 지도 수명 = 임무 수명. 지도가 임무를 넘어 살아남으면 두 번째 탐사가
        # frontier 즉시 소진으로 2초 만에 완료된다(임무 47bac6ed 실사례).
        # 재개(RESUME)에는 걸리지 않는다 — MISSION_START 새 임무 전이에서만 동작.
        self.declare_parameter('reset_map_on_start', True)
        self.declare_parameter('max_interaction_seconds', 300)
        self.declare_parameter('person_lost_seconds', 3.0)
        self.declare_parameter('tick_period_seconds', 0.25)
        # `/mission/status` 재발행 주기 (S15P11A301-320).
        #
        # **소비자들이 이 토픽의 신선도로 정지를 판정한다.** 전이 때만 발행하면
        # 상태가 안 바뀌는 정상 주행이 곧 차단 조건이 된다 — 실측에서 「탐사 시작」
        # 10초 뒤 `safety_gate` 가 `MISSION_STALE` 로 속도를 0 으로 만들었고,
        # `exploration` 은 3초에 목표 선택을 멈췄다.
        #
        # 그쪽 판정을 무르게 하는 대신 여기서 heartbeat 를 낸다. 두 소비자가 그
        # 판정을 갖는 근거가 정당하기 때문이다 — latched 값을 무한 신뢰하면
        # 이 노드가 죽어도 아무도 로봇을 세우지 않는다(`gate.py`).
        #
        # 1Hz 는 가장 촘촘한 소비자(`exploration` 3초)에 3번의 여유를 준다.
        self.declare_parameter('status_heartbeat_seconds', 1.0)
        # 부팅 직후 자동으로 탐사를 시작하지 않는다. 26.4가 "재시작 후 진행 중이던
        # 임무를 자동 주행으로 복구하지 않는다"고 정했다. 개발 중 편의를 위한
        # 파라미터이며 운영에서는 false로 둔다.
        self.declare_parameter('auto_start', False)
        # encounter 위치 스탬프용 TF 프레임 (S15P11A301-170). cloud_bridge와
        # 같은 기본값이다 — 두 노드가 같은 좌표를 봐야 telemetry의 로봇 위치와
        # encounter 위치가 한 지도에서 맞는다.
        self.declare_parameter('map_frame', 'map')
        # map_uploader가 임무 시작에 발급한 mapId(S15P11A301-193). S15P11A301-170이
        # pose.mapId를 null로 비워 둔 자리를 이 값이 채운다.
        self.declare_parameter(
            'map_registered_topic', '/map_uploader/registered'
        )
        self.declare_parameter('base_frame', 'base_footprint')

        # 모드 전환 (S15P11A301-298). 토픽은 esp32_motor_bridge 의 것과 같아야 한다.
        self.declare_parameter(
            'drive_state_topic', '/esp32_motor_bridge/drive_state'
        )
        self.declare_parameter(
            'command_ack_topic', '/esp32_motor_bridge/command_ack'
        )
        self.declare_parameter('set_mode_topic', '/esp32_motor_bridge/set_mode')
        self.declare_parameter('manual_confirm_seconds', 0.10)
        self.declare_parameter('set_mode_ack_timeout_seconds', 0.5)
        self.declare_parameter('drive_state_ttl_seconds', 0.5)

        self.machine = MissionStateMachine(
            post_recording_seconds=int(self._param('post_recording_seconds')),
            max_interaction_seconds=int(self._param('max_interaction_seconds')),
            person_lost_seconds=float(self._param('person_lost_seconds')),
        )
        self.gateway = ModeGateway(
            manual_confirm_seconds=float(self._param('manual_confirm_seconds')),
            drive_state_ttl_seconds=float(self._param('drive_state_ttl_seconds')),
            ack_timeout_seconds=float(self._param('set_mode_ack_timeout_seconds')),
        )

        # encounter는 잃으면 이벤트가 사라지므로 RELIABLE이다. 녹화 노드가 기본
        # QoS(RELIABLE, depth 10)로 구독하므로 맞아야 한다. BEST_EFFORT로 발행하면
        # 녹화 노드가 한 건도 받지 못한다(S15P11A301-123에서 /scan으로 겪었다).
        reliable = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.encounter_pub = self.create_publisher(
            String, self._param('encounter_topic'), reliable
        )
        # status는 TRANSIENT_LOCAL이다. MQTT Retain에 대응하는 ROS 설정이다.
        #
        # 이 토픽은 상태가 바뀔 때만 발행한다. 그래서 나중에 뜬 구독자는 다음 전이가
        # 일어날 때까지 현재 상태를 모른다. 전이는 몇 분에 한 번일 수 있다.
        # `cloud_bridge`가 그 사이에 관제로 "임무 상태 모름"을 계속 보내게 된다.
        #
        # TRANSIENT_LOCAL이면 늦게 붙은 구독자도 마지막 값을 즉시 받는다. 31-4가
        # state 채널에 Retain을 쓴 것과 같은 이유다.
        status_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.status_pub = self.create_publisher(
            String, self._param('status_topic'), status_qos
        )

        # 관제 명령 처리 결과 (S15P11A301-143). `cloud_bridge`가 이것을 MQTT `acks`로
        # 회신한다.
        #
        # 본문 형식이 `command-ack.schema.json`과 같다. bridge가 봉투만 씌운다.
        # encounter를 그렇게 다루는 것과 같은 구조다 — 판단은 상태 머신이 하고
        # bridge는 전달만 한다(26.1의 단일 권한).
        #
        # RELIABLE이어야 한다. 잃으면 관제의 control_commands.result가 영원히
        # PENDING으로 남고 조작자는 명령이 먹혔는지 알 수 없다.
        self.command_result_pub = self.create_publisher(
            String, self._param('command_result_topic'), reliable
        )

        # 사람 후보는 프레임마다 오고 한 프레임을 놓쳐도 다음이 온다. 탐지 노드가
        # BEST_EFFORT로 발행할 가능성이 높으므로 이쪽도 BEST_EFFORT로 둔다.
        # RELIABLE 구독자는 BEST_EFFORT 발행자와 매칭되지 않아 한 건도 받지 못한다.
        best_effort = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=5,
        )
        self.create_subscription(
            String,
            self._param('candidates_topic'),
            self._on_candidates,
            best_effort,
        )
        # 신호는 드물고 잃으면 상태가 멈춘다. RELIABLE이다.
        self.create_subscription(
            String, self._param('signal_topic'), self._on_signal, reliable
        )
        # map_uploader가 발급한 mapId (S15P11A301-193). status_qos와 같은
        # TRANSIENT_LOCAL이라 이 노드가 나중에 떠도 현재 임무의 값을 받는다.
        self.create_subscription(
            String,
            self._param('map_registered_topic'),
            self._on_map_registered,
            status_qos,
        )

        # ---- 모드 전환 (S15P11A301-298) ----
        #
        # 요청은 RELIABLE 이다. 잃으면 관제가 500ms 뒤 MOTOR_BOARD_NO_ACK 를 보고
        # "보드가 죽었다"로 읽는데, 실제로는 프레임이 나가지도 않은 것이다.
        self.set_mode_pub = self.create_publisher(
            String, self._param('set_mode_topic'), reliable
        )
        # DRIVE_STATE 는 50Hz 사실 스트림이다. **백로그를 쌓으면 안 된다** —
        # depth 1 BEST_EFFORT 로 최신 한 건만 본다. 여기서 읽는 것은 `state`
        # 하나뿐이고, 늦게 도착한 옛 프레임은 판단을 흐릴 뿐이다.
        # (mission_manager 는 이미 CPU 포화가 보고된 노드다 — S15P11A301-249.)
        drive_state_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            String,
            self._param('drive_state_topic'),
            self._on_drive_state,
            drive_state_qos,
        )
        # ACK 는 RELIABLE 이다. 잃으면 거부가 타임아웃으로 오표기되어 운영자가
        # 「모터 보드 무응답」을 보는데 실제로는 「조종 중이라 거부」였다.
        self.create_subscription(
            String,
            self._param('command_ack_topic'),
            self._on_command_ack,
            reliable,
        )

        # TF는 encounter 위치 스탬프에만 쓴다 (S15P11A301-170). SLAM이 없으면
        # 조회가 실패하고 pose는 null이 된다 — 값을 지어내지 않는다. 관제가
        # "위치 모름"과 "원점"을 구별해야 한다(cloud_bridge와 같은 원칙).
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        # 확정 시점의 위치. 스키마가 pose를 "확정 시점의 로봇 위치"로 정의하므로
        # CONFIRMED에서 한 번 조회해 같은 encounter의 이후 phase에 재사용한다.
        # phase마다 새로 조회하면 LOST의 pose가 확정 시점과 달라진다.
        self._confirmed_pose: dict | None = None
        self._confirmed_pose_encounter: str | None = None
        # 조회 실패 로그는 상태가 바뀔 때만 남긴다. 후보가 계속 오면 발행도
        # 잦아서, 매번 경고하면 로그가 그 소리로 가득 찬다.
        self._pose_available = False
        # map_uploader가 발급한 지도 식별자. 못 받았으면 None이고 encounter의
        # pose.mapId도 null이다 — 지어내면 관제가 다른 지도의 좌표로 해석한다.
        self._map_id: str | None = None

        self.create_timer(float(self._param('tick_period_seconds')), self._on_tick)
        # 마지막으로 발행한 상태 페이로드. heartbeat 가 **그대로** 다시 낸다
        # (S15P11A301-320). 새로 만들지 않는 이유는 `changedAt`·`previousState`·
        # `reason` 이 전이의 사실이기 때문이다 — heartbeat 가 그것을 지금 시각으로
        # 덮으면 「방금 상태가 바뀌었다」는 거짓말이 매초 나간다.
        self._last_status_json: str | None = None
        self.create_timer(
            float(self._param('status_heartbeat_seconds')), self._republish_status
        )

        self._published_status = False
        self.get_logger().info(
            f'mission_manager 시작. 상태={self.machine.state.value} '
            f'후보={self._param("candidates_topic")} '
            f'신호={self._param("signal_topic")} '
            f'→ encounter={self._param("encounter_topic")} '
            f'status={self._param("status_topic")}'
        )
        self._publish_status(reason='node started')

        if bool(self._param('auto_start')):
            self.get_logger().warn(
                'auto_start가 켜져 있다. 26.4는 재시작 후 자동 주행 복구를 '
                '금지하므로 운영에서는 끈다.'
            )
            self._apply(
                self.machine.handle_signal(Signal.MISSION_START, now=self._now())
            )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _monotonic() -> float:
        """`mode_gateway` 전용 시계 (S15P11A301-298).

        벽시계를 쓰면 NTP 보정 한 번에 500ms 창이 뒤틀린다. `sentinel_safety/gate.py`
        와 같은 규약이고, 상태기계에 넘기는 `_now()` 와는 **다른 시계다** — 섞지 않는다.
        """
        return time.monotonic()

    # ------------------------------------------------------------------
    # 입력
    # ------------------------------------------------------------------

    def _on_map_registered(self, message: String) -> None:
        """map_uploader가 발급한 mapId를 받아 둔다.

        encounter의 `pose.mapId`가 이 값을 쓴다. S15P11A301-170이 소유자가
        정해지기 전까지 null로 비워 둔 자리다.
        """
        try:
            body = json.loads(message.data)
        except json.JSONDecodeError:
            return
        if not isinstance(body, dict):
            return
        map_id = body.get('mapId')
        if not map_id or map_id == self._map_id:
            return
        self._map_id = str(map_id)
        self.get_logger().info(f'지도 세션 수신. mapId={self._map_id}')

    def _on_candidates(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'후보 JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict):
            self.get_logger().warn('후보 본문이 객체가 아니다')
            return

        candidates = payload.get('candidates')
        if not isinstance(candidates, list):
            # 빈 배열과 키 누락은 다르다. 누락은 계약 위반이므로 사람이 알아야 한다.
            self.get_logger().warn(
                'candidates 배열이 없다. person-candidates.schema.json을 확인한다. '
                '사람이 없으면 빈 배열을 보낸다.'
            )
            return

        track_ids: set[int] = set()
        best_confidence: float | None = None
        for item in candidates:
            if not isinstance(item, dict):
                continue
            track = item.get('trackId')
            if isinstance(track, int):
                track_ids.add(track)
            confidence = item.get('confidence')
            if isinstance(confidence, (int, float)):
                best_confidence = max(best_confidence or 0.0, float(confidence))

        observed_at = self._parse_time(payload.get('observedAt')) or self._now()

        transition = self.machine.observe_candidates(
            now=observed_at,
            track_ids=track_ids,
            confidence=best_confidence,
            new_encounter_id=str(uuid.uuid4()),
        )
        self._apply(transition, at=observed_at)

    def _on_signal(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'신호 JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict):
            self.get_logger().warn('신호 본문이 객체가 아니다')
            return

        raw = payload.get('signal')
        try:
            signal = Signal(raw)
        except ValueError:
            self.get_logger().warn(
                f'모르는 signal "{raw}". mission-signal.schema.json의 enum을 쓴다.'
            )
            return

        # 모드 요청은 상태기계에 닿기 전에 가로챈다 (S15P11A301-298). 보드가
        # 거부할 수 있으므로 의도를 바로 전이로 바꿀 수 없다.
        requested_mode = _MODE_REQUEST_SIGNALS.get(signal)
        if requested_mode is not None:
            self._on_mode_request(requested_mode, payload)
            return

        sent_at = self._parse_time(payload.get('sentAt')) or self._now()
        transition = self.machine.handle_signal(
            signal,
            now=sent_at,
            encounter_id=payload.get('encounterId'),
            mission_id=payload.get('missionId'),
            command_id=payload.get('commandId'),
            detail=str(payload.get('detail') or ''),
        )
        if signal is Signal.MISSION_START and transition.changed:
            self._reset_slam_map()
            if self.machine.mission_id:
                self.get_logger().info(
                    f'임무 시작. missionId={self.machine.mission_id[:8]}'
                )
            else:
                # 임무 없이 시작하면 발행하는 encounter가 서버에 기록되지 않는다.
                # 조용히 넘기면 "왜 관제에 안 보이나"를 한참 찾게 된다.
                self.get_logger().warn(
                    'missionId 없이 임무를 시작했다. encounter는 발행되지만 '
                    '백엔드가 적재하지 않는다(encounters.mission_id NOT NULL). '
                    '관제에서 임무를 만들어 MISSION_START에 missionId를 담는다.'
                )
        command_id = payload.get('commandId')
        if command_id:
            # 관제에서 온 명령이다. 결과를 회신해야 control_commands.result가
            # PENDING에서 벗어난다(27.4). ROS 내부 신호는 commandId가 없으므로
            # 여기 걸리지 않는다.
            self._publish_command_result(str(command_id), transition)

        source = payload.get('source') or '?'
        if transition.ignored_reason:
            # 무시한 것을 조용히 넘기지 않는다. "수신하지 못한 것"과 "무시한 것"을
            # 구별할 수 없으면 통합 시험에서 원인을 찾을 수 없다.
            self.get_logger().info(
                f'{signal.value}({source}) 무시: {transition.ignored_reason}'
            )
        self._apply(transition, at=sent_at)

    def _on_tick(self) -> None:
        self._apply(self.machine.tick(self._now()))
        # ACK 타임아웃과 DRIVE_STATE 스테일 판정 (S15P11A301-298).
        self._apply_outcome(self.gateway.tick(self._monotonic()))

    # ------------------------------------------------------------------
    # 모드 전환 (S15P11A301-298)
    # ------------------------------------------------------------------

    def _on_mode_request(self, mode: str, payload: dict) -> None:
        """관제 「수동」·「자율」. 보드에 물어보고 답이 온 뒤에야 상태를 바꾼다."""
        command_id = payload.get('commandId')
        outcome = self.gateway.request(
            mode,
            command_id=str(command_id) if command_id else None,
            now=self._monotonic(),
            mission_state=self.machine.state.value,
            # 구독자가 0명이면 프레임이 어디에도 가지 않는다. 500ms 를 기다려
            # 타임아웃을 내는 것보다 즉시 사실대로 거부하는 편이 낫다.
            bridge_alive=self.set_mode_pub.get_subscription_count() > 0,
        )
        self._apply_outcome(outcome)

    def _on_drive_state(self, message: String) -> None:
        """50Hz `DRIVE_STATE`. `state` 하나만 읽는다.

        나머지 필드(PWM·fault·조향)는 이 노드의 관심사가 아니다 — 진단은
        `esp32_motor_bridge` 가 `/diagnostics` 로 낸다.
        """
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError:
            # 50Hz 스트림이라 로그를 남기면 그 소리로 가득 찬다. 브리지 쪽이
            # 이미 파싱 오류를 카운트한다.
            return
        if not isinstance(payload, dict):
            return
        board_state = payload.get('state')
        if not isinstance(board_state, str):
            return
        self._apply_outcome(
            self.gateway.observe_drive_state(board_state, now=self._monotonic())
        )

    def _on_command_ack(self, message: String) -> None:
        """`COMMAND_ACK`. `SET_MODE` 에 대한 것만 게이트웨이가 본다."""
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f'command_ack JSON 해석 실패: {error}')
            return
        if not isinstance(payload, dict):
            return
        self._apply_outcome(
            self.gateway.observe_ack(
                acked_message_name=str(payload.get('acked_message_type_name') or ''),
                result_name=str(payload.get('result_name') or ''),
                # `board_state` 는 브리지가 이미 이름으로 바꿔 실어 준다.
                board_state=str(payload.get('board_state') or ''),
                now=self._monotonic(),
            )
        )

    def _apply_outcome(self, outcome) -> None:
        """`ModeOutcome` 하나를 집행한다. 넷이 독립이라 순서만 맞추면 된다.

        프레임 → 전이 → ACK 순이다. ACK 를 먼저 내면 관제가 「됐다」를 본 뒤에
        `/mission/status` 가 따라오고, 그 사이에 화면이 옛 상태로 한 번 깜박인다.
        """
        if outcome.empty:
            return

        if outcome.set_mode is not None:
            self.set_mode_pub.publish(
                String(data=json.dumps({'mode': outcome.set_mode}, ensure_ascii=False))
            )

        if outcome.signal is not None:
            self._engage(outcome.signal)

        if outcome.ack is not None:
            self._publish_ack_body(outcome.ack)

        if outcome.note:
            if outcome.stale:
                self.get_logger().warn(outcome.note)
            else:
                self.get_logger().info(outcome.note)

    def _reset_slam_map(self) -> None:
        """새 임무 = 새 지도 (S15P11A301-362).

        slam 프로세스에 종료 신호를 보내면 launch 의 respawn 이 빈 지도로
        되살린다(slam.launch.py 의 짝 주석). 재시작 공백 3~4초는 탐사의
        pose 대기가 흡수한다. RESUME(재개) 경로에서는 호출되지 않는다 —
        일시정지는 지도를 유지한다(37-3).
        """
        if not bool(self._param('reset_map_on_start')):
            return
        if reset_slam_process():
            self.get_logger().info(
                '새 임무 — slam 재시작으로 지도를 초기화한다. '
                'map frame 이 3~4초 사라졌다 현재 위치를 원점으로 돌아온다'
            )
        else:
            # enable_slam 없이 도는 구성(무지도 검증)에서는 정상이다. 지도가
            # 있어야 하는 구성에서 이 경고가 보이면 respawn 체인부터 의심하라.
            self.get_logger().warn(
                'slam 프로세스를 찾지 못해 지도 초기화를 건너뛴다 — '
                '이전 임무의 지도가 남아 있으면 탐사가 즉시 완료될 수 있다'
            )

    def _engage(self, signal: Signal) -> None:
        """보드가 알린 사실을 상태기계에 넣는다.

        `commandId` 를 넘기지 않는다. ACK 는 `ModeOutcome.ack` 가 이미 들고 있고,
        여기서 또 넘기면 `_publish_command_result` 가 두 번째 답을 내 백엔드가
        앞의 것을 덮는다.
        """
        moment = self._now()
        self._apply(self.machine.handle_signal(signal, now=moment), at=moment)

    # ------------------------------------------------------------------
    # 관제 명령 결과 (S15P11A301-143)
    # ------------------------------------------------------------------

    def _publish_command_result(self, command_id: str, transition) -> None:
        """명령 하나의 처리 결과를 낸다. 계약은 `command-ack.schema.json`이다.

        상태가 바뀌었으면 `EXECUTED`다. `ACCEPTED`를 쓰지 않는 이유는 여기서 다루는
        명령이 전부 동기적으로 끝나기 때문이다. 접수만 하고 나중에 완료되는 것은
        `RETURN`(복귀 주행)뿐이고 그것은 아직 구현하지 않았다. 실제로 끝났는데
        `ACCEPTED`를 보내면 관제가 완료를 기다리며 멈춘다.

        상태가 안 바뀌었어도 **원하는 상태에 이미 있으면 성공으로 본다.** 조작자가
        PAUSE를 두 번 눌렀을 때 거부가 뜨면 무엇이 잘못됐는지 찾게 된다. 상태 머신이
        그 경우 `reason_code`를 비워 두므로 그것으로 구분한다.

        거부는 `reason_code`가 있을 때뿐이다. 그 값이 관제 화면의 분기 기준이다.
        """
        rejected = transition.reason_code is not None
        body = {
            'commandId': command_id,
            'status': ACK_REJECTED if rejected else ACK_EXECUTED,
            'reasonCode': transition.reason_code,
            # 사람이 읽을 설명. 상태 머신이 남긴 사유를 그대로 넘긴다.
            'message': (
                transition.ignored_reason
                or transition.reason
                or None
            ) or None,
        }
        self._publish_ack_body(body)
        if rejected:
            self.get_logger().warn(
                f'명령 거부 {command_id[:8]} {transition.reason_code}: '
                f'{transition.ignored_reason}'
            )
        else:
            self.get_logger().info(
                f'명령 처리 {command_id[:8]} EXECUTED → {transition.state.value}'
            )

    def _publish_ack_body(self, body: dict) -> None:
        """`command-ack.schema.json` 본문 하나를 발행한다.

        상태기계 경로와 `mode_gateway` 경로가 같은 꼬리를 쓴다. 두 곳에서 각자
        직렬화하면 한쪽만 필드를 고치는 일이 생긴다.
        """
        self.command_result_pub.publish(
            String(data=json.dumps(body, ensure_ascii=False))
        )

    @staticmethod
    def _parse_time(raw) -> datetime | None:
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # 출력
    # ------------------------------------------------------------------

    def _apply(self, transition, at: datetime | None = None) -> None:
        """전이 결과를 토픽으로 낸다."""
        moment = at or self._now()

        if transition.phase is not None:
            # 전이가 대상 encounter 를 들고 온다 (S15P11A301-276). 여기서
            # machine.encounter 를 다시 읽으면, 전이 중에 encounter 를 버리는
            # 핸들러(임무 종료 등)와 순서가 엉켜 encounterId 가 빠진다.
            self._publish_encounter(transition.phase, moment, transition.encounter)

        if transition.changed:
            self.get_logger().info(
                f'{(transition.previous or MissionState.SAFE_IDLE).value} → '
                f'{transition.state.value}  ({transition.reason})'
                + (
                    f'  encounter={self.machine.encounter_id[:8]}'
                    if self.machine.encounter_id
                    else ''
                )
            )
            if transition.state in UNIMPLEMENTED:
                # 지금은 RETURNING 하나이며 여전히 도달 불가다 —
                # `COMMAND_TO_SIGNAL` 에 RETURN 이 없어 신호 자체가 만들어지지
                # 않는다. 그래도 경고를 남기는 이유는, 도달했다면 그 자체가
                # 계약 어딘가가 깨졌다는 뜻이기 때문이다.
                self.get_logger().warn(
                    f'{transition.state.value}는 아직 구현하지 않은 상태다. '
                    '전이 트리거가 없어야 하는데 도달했다.'
                )
            self._publish_status(
                reason=transition.reason, previous=transition.previous, at=moment
            )
        elif transition.reason:
            # 상태는 그대로인데 personCount 같은 값이 바뀐 경우다.
            self._publish_status(reason=transition.reason, at=moment)

        # 수동 진입 2단 전이의 남은 단을 **같은 콜백에서** 밀어낸다
        # (S15P11A301-298). 주기 타이머를 기다리면 관제가 최대 250ms 동안
        # 「일시정지」로 보이고, 그 화면은 사실과 다르다 - 보드는 이미 사람에게
        # 바퀴를 넘긴 뒤다.
        #
        # 재귀 깊이는 1 이다. `tick()` 이 pending 을 먼저 내리므로 두 번째 호출에서
        # `pending_step` 은 반드시 거짓이다.
        if self.machine.pending_step:
            self._apply(self.machine.tick(moment), at=moment)

    def _publish_encounter(
        self, phase: Phase, moment: datetime, encounter=None
    ) -> None:
        # 전이가 실어 준 encounter 를 우선 쓴다. 없으면 현재 상태에서 읽는다 —
        # 종전 동작이며, phase 를 내는 모든 경로가 전이를 거치므로 실제로는
        # 앞쪽이 쓰인다 (S15P11A301-276).
        if encounter is None:
            encounter = self.machine.encounter
        if encounter is None:
            self.get_logger().error(
                f'encounter 없이 {phase.value}를 낼 수 없다. 상태 머신 결함이다.'
            )
            return

        if self.encounter_pub.get_subscription_count() == 0:
            # 발행은 하되 알린다. 녹화 노드가 아직 안 떴으면 이 이벤트는 사라진다.
            self.get_logger().warn(
                f'{phase.value}를 낼 때 구독자가 없다. recording_manager가 '
                '떠 있는지 확인한다. 이 이벤트는 녹화되지 않는다.'
            )

        body = {
            'encounterId': encounter.encounter_id,
            'phase': phase.value,
            'detectedAt': format_utc(encounter.detected_at),
            'personCount': encounter.person_count,
            'trackIds': sorted(encounter.track_ids) or None,
            'confidence': encounter.confidence,
            # 확정 시점의 로봇 위치 (S15P11A301-170). SLAM이 없으면 null이고
            # 스키마가 그것을 "SLAM이 없으면 null"로 정의한다(25.3).
            'pose': self._pose_for(encounter.encounter_id, phase),
            'missionId': self.machine.mission_id,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.encounter_pub.publish(message)

    def _pose_for(self, encounter_id: str, phase: Phase) -> dict | None:
        """이 encounter에 실을 위치를 돌려준다.

        CONFIRMED에서 조회해 캐시하고, 같은 encounter의 이후 phase는 캐시를
        재사용한다. 스키마의 pose가 "확정 시점의 로봇 위치"이기 때문이다 —
        발행 시점마다 새로 읽으면 LOST의 pose가 확정 시점과 달라지고, 백엔드는
        INSERT(CONFIRMED) 값만 저장하므로 두 값이 어긋나면 조사할 때 헷갈린다.
        """
        if encounter_id != self._confirmed_pose_encounter:
            self._confirmed_pose = self._lookup_pose()
            self._confirmed_pose_encounter = encounter_id
            if self._confirmed_pose is None and phase is Phase.CONFIRMED:
                self.get_logger().info(
                    f'{encounter_id[:8]} 확정 시점에 TF(map→base) 조회 실패. '
                    'pose 없이 발행한다. SLAM이 떠 있는지 확인한다.'
                )
        return self._confirmed_pose

    def _lookup_pose(self) -> dict | None:
        """map → base_footprint 를 encounter pose로 바꾼다. 실패하면 None."""
        try:
            transform = self.tf_buffer.lookup_transform(
                str(self._param('map_frame')),
                str(self._param('base_frame')),
                rclpy.time.Time(),
            )
        except (
            tf2_ros.LookupException,
            tf2_ros.ConnectivityException,
            tf2_ros.ExtrapolationException,
        ):
            self._pose_available = False
            return None

        if not self._pose_available:
            self.get_logger().info('TF(map→base) 조회 성공. encounter에 위치를 싣는다.')
        self._pose_available = True
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return encounter_pose(
            translation.x,
            translation.y,
            (rotation.x, rotation.y, rotation.z, rotation.w),
            map_id=self._map_id,
        )

    def _publish_status(
        self,
        *,
        reason: str = '',
        previous: MissionState | None = None,
        at: datetime | None = None,
    ) -> None:
        machine = self.machine
        body = {
            'state': machine.state.value,
            # `command_mux` 가 이 값으로 자율/수동을 고른다 (S15P11A301-278).
            #
            # 없으면 mux 는 **모든 명령을 0으로 막는다** — "모르면 기본값을 자율로
            # 두지 않는다"는 그쪽 규칙 때문이다(mux.py). 그것은 옳은 설계이고,
            # 문제는 소유자가 값을 내보내지 않는 것이었다. `mux.py` 주석이
            # 「controlMode 는 mission_manager 가 소유한다」로 계약을 적어 두었는데
            # 발행 쪽이 비어 있었다.
            #
            # 종전에는 안전 체인을 켜도 로봇이 움직이지 않았고, 원인 표시는
            # mux 의 경고 한 줄뿐이었다. S15P11A301-237 의 관통 시험이 이것을
            # 놓친 이유는 가짜 상태 메시지에 controlMode 를 직접 넣어 통과시켰기
            # 때문이다 — 조건이 실제 스택과 달랐다.
            #
            # 파생 규칙은 `cloud_bridge` 가 관제로 보낼 때 쓰던 것과 같다
            # (state == MANUAL ? MANUAL : AUTO). 어휘는 state.schema.json 을
            # 따른다.
            'controlMode': machine.control_mode,
            'movementAllowed': machine.movement_allowed,
            'speedLimit': machine.speed_limit,
            'changedAt': format_utc(at or self._now()),
            'previousState': previous.value if previous else None,
            'reason': reason or None,
            'encounterId': machine.encounter_id,
            # 이 토픽만 보고 "지금 어느 임무인가"를 알 수 있어야 한다
            # (S15P11A301-171). 없으면 구독자가 /mission/signal의 MISSION_START를
            # 따로 엿봐야 하고, 늦게 뜬 노드는 그 값을 영영 못 받는다.
            'missionId': machine.mission_id,
            'personCount': machine.person_count,
            # 26.4의 복구 판단은 checkpoint를 읽어야 하며 이 티켓 범위가 아니다.
            'recoveryRequired': False,
        }
        message = String()
        message.data = json.dumps(body, ensure_ascii=False)
        self.status_pub.publish(message)
        self._last_status_json = message.data
        self._published_status = True

    def _republish_status(self) -> None:
        """마지막 상태를 그대로 다시 낸다 (S15P11A301-320).

        전이가 아니라 **생존 신호**다. 그래서 페이로드를 다시 만들지 않는다 —
        구독자가 받는 것은 직전 전이와 바이트까지 같은 메시지이고, 달라지는 것은
        받은 시각뿐이다. 그 시각이 `safety_gate`·`exploration` 의 stale 판정이
        보는 값이다.

        `map_saver` 처럼 특정 상태를 **사건으로** 읽는 소비자가 있으므로 같은
        메시지가 반복돼도 안전해야 한다. 그쪽은 `_saved_missions` 로 임무당 한 번을
        보장한다 — 이 heartbeat 때문에 생긴 규칙이 아니라, TRANSIENT_LOCAL 로 늦게
        뜬 구독자가 마지막 값을 받는 경로 때문에 이미 있던 것이다.
        """
        if self._last_status_json is None:
            return
        message = String()
        message.data = self._last_status_json
        self.status_pub.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MissionManagerNode()
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

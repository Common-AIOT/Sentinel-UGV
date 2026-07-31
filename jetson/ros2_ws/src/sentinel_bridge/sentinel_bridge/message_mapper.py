"""ROS 상태를 공통 봉투(명세 31-5)로 감싼다 (S15P11A301-128).

이 모듈은 MQTT를 모른다. 순수 변환만 하므로 브로커 없이 단위 시험할 수 있고,
`common/schemas`로 검증할 수 있다. 발행은 `mqtt_client`가, 수집은
`cloud_bridge_node`가 담당한다.

**미확보 필드는 null로 보낸다.** ESP32 연동(S15P11A301-84~86)이 끝나기 전에는
`environment`, `battery`, `motion`, `health.mcuConnected`가 null이다. 필드를
나중에 추가하면 백엔드 파싱과 DB 스키마, 프런트엔드 표시를 다시 건드려야 하므로
처음부터 31-6 전체 형태를 보낸다.

`null`과 `false`는 다르다. `health.mcuConnected`가 `false`면 확인했고 끊긴
것이고, `null`이면 확인할 수단 자체가 없는 것이다. 관제 화면이 "연결 끊김"과
"미구현"을 구분해야 하므로 섞지 않는다.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"

MESSAGE_TYPE_PRESENCE = "ROBOT_PRESENCE"
MESSAGE_TYPE_STATE = "ROBOT_STATE"
MESSAGE_TYPE_TELEMETRY = "ROBOT_TELEMETRY"
MESSAGE_TYPE_ENCOUNTER = "ENCOUNTER_CONFIRMED"
MESSAGE_TYPE_INTERACTION_REPORT = "INTERACTION_REPORT"
MESSAGE_TYPE_COMMAND_ACK = "COMMAND_ACK"

# 백엔드가 cmd/mission 으로 보내는 봉투의 messageType (S15P11A301-141).
# 구독한 메시지를 검증할 때 쓴다.
MESSAGE_TYPE_MISSION_COMMAND = "MISSION_COMMAND"

# cmd/mission 의 type → /mission/signal 의 signal (S15P11A301-143).
#
# RETURN 이 없는 것이 의도다. RETURNING 은 home pose 복귀 주행이 필요해
# 미구현이므로(UNIMPLEMENTED) 신호를 만들지 않고 bridge 가 NOT_IMPLEMENTED 로
# 거부한다. 조용히 무시하면 관제가 영원히 PENDING 을 본다.
#
# RESUME 이 RESUME_APPROVED 인 이유는 두 재개 신호의 역할이 다르기 때문이다.
# RESUME_REQUESTED 는 음성 쪽이 보고를 마치고 탐사를 이어가겠다는 요청이며
# REPORTING 에서만 유효하다. PAUSED 를 푸는 것은 30.5 가 자동 재개를 금지했으므로
# 운영자의 명시적 재개, 즉 RESUME_APPROVED 뿐이다.
COMMAND_TO_SIGNAL: dict[str, str] = {
    "START": "MISSION_START",
    "PAUSE": "PAUSE_REQUESTED",
    "RESUME": "RESUME_APPROVED",
    "STOP": "MISSION_COMPLETED",
}

# 26.2의 임무 상태를 31-5 state 채널의 `safetyState` enum으로 옮긴다.
#
# 두 값의 목적이 다르다. `missionState`는 "지금 무엇을 하는 중인가"이고
# `safetyState`는 "안전하게 멈춰 있는가"다. 관제 화면은 후자로 정지 표시를 낸다.
#
# 여기(rclpy를 import하지 않는 모듈)에 두는 이유는 CI에서 검증하기 위해서다.
# 노드 파일에 두면 시험이 rclpy를 끌어와 ROS 없는 컨테이너에서 실패한다.
# `message_mapper`와 `mqtt_client`가 ROS를 모르게 유지하는 것이 이 패키지의
# 규칙이며, 매핑은 순수 데이터이므로 여기가 제자리다.
#
# dict로 두고 `.get()`의 기본값을 쓰지 않는다. 상태가 추가될 때 조용히 RUNNING이
# 되면 정지해야 하는 상태가 관제에 주행 중으로 보인다.
SAFETY_STATE_BY_MISSION_STATE = {
    "SAFE_IDLE": "SAFE_IDLE",
    "EXPLORING": "RUNNING",
    "PERSON_APPROACHING": "RUNNING",
    # 사람과 대화하는 동안은 정지 상태다(26.2 이동 불허).
    "INTERACTING": "STOPPED",
    "POST_RECORDING": "STOPPED",
    "REPORTING": "STOPPED",
    "PAUSED": "STOPPED",
    # deadman이 눌린 동안만 움직인다. 그 판단은 조종 노드가 하며 여기서는
    # 모드가 수동이라는 사실만 전한다.
    "MANUAL": "RUNNING",
    "RETURNING": "RUNNING",
    # 임무가 끝나 다음 임무를 받을 수 있는 상태다.
    "COMPLETED": "READY",
    "ESTOP": "ESTOP",
    "ERROR": "FAULT",
}

# 이 상태의 telemetry를 임무에 귀속시킬 것인가 (S15P11A301-190).
#
# `/mission/status`는 TRANSIENT_LOCAL이라 임무가 끝나도 COMPLETED가 계속 남아
# 있고 그 페이로드는 missionId를 담고 있다(S15P11A301-171). 그 값을 무조건 쓰면
# **임무 종료 후의 위치가 완료된 임무의 궤적에 섞인다** — 로봇을 손으로 들어
# 옮기는 중일 수도 있다. 그래서 상태로 걸러야 한다.
#
# SAFETY_STATE_BY_MISSION_STATE와 같은 규칙으로 dict에 전수를 적고 `.get()`의
# 기본값을 쓰지 않는다. 상태가 추가될 때 조용히 True가 되면 임무 밖 데이터가
# 궤적을 오염시키고, 조용히 False가 되면 임무 중 궤적이 통째로 사라진다.
# 어느 쪽이든 화면을 보고 알아내기 어렵다.
MISSION_ACTIVE_BY_STATE = {
    # 임무를 받기 전이다. 이 구간의 telemetry는 어느 임무에도 속하지 않는다.
    "SAFE_IDLE": False,
    "EXPLORING": True,
    "PERSON_APPROACHING": True,
    "INTERACTING": True,
    "POST_RECORDING": True,
    "REPORTING": True,
    # 멈춰 있어도 임무 중이다. 어디서 멈췄는지가 기록될 값이다.
    "PAUSED": True,
    "MANUAL": True,
    "RETURNING": True,
    # 임무가 끝났다. 이후 telemetry는 다음 임무를 기다리는 구간이다.
    "COMPLETED": False,
    # 임무 중 비상정지·오류다. 어디서 그랬는지가 사후 분석의 핵심이라
    # 반드시 귀속시킨다. 임무 밖에서 났다면 status의 missionId가 애초에
    # null이므로 여기서 True여도 null이 나간다.
    "ESTOP": True,
    "ERROR": True,
}


def active_mission_id(status: dict | None) -> str | None:
    """`/mission/status` 페이로드에서 귀속시킬 missionId를 꺼낸다.

    상태가 활성이 아니거나 모르는 상태면 None이다. 모르는 상태를 활성으로 보지
    않는 것은 오염이 누락보다 고치기 어렵기 때문이다 — 누락은 궤적이 비어
    "안 나온다"로 바로 보이지만, 오염은 그럴싸한 궤적 안에 섞여 보이지 않는다.
    """
    if not status:
        return None
    if not MISSION_ACTIVE_BY_STATE.get(status.get("state"), False):
        return None
    mission_id = status.get("missionId")
    return str(mission_id) if mission_id else None


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """쿼터니언에서 yaw(라디안)만 뽑는다.

    2D SLAM이므로 roll·pitch는 쓰지 않는다. `telemetry.schema.json`의 `pose.yaw`가
    "라디안, REP-103에 따라 반시계 방향이 양수"로 정의돼 있다.

    `tf_transformations`를 쓰지 않는 이유는 그 패키지가 이 젯슨에 없고
    (`transforms3d` 의존), 필요한 것이 이 한 줄이기 때문이다. 의존성을 하나 더
    늘리는 대신 공식을 적는다.

    여기(rclpy를 import하지 않는 모듈)에 두면 CI에서 검증된다. 노드 파일에 두면
    시험이 rclpy를 끌어와 ROS 없는 컨테이너에서 실패한다(S15P11A301-135).
    """
    # ZYX 순서 오일러 각의 yaw 항. atan2를 쓰므로 -pi..pi 범위가 나온다.
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def utc_now_iso() -> str:
    """31-5의 `sentAt` 형식. UTC만 쓰고 반드시 Z로 끝난다.

    지역 시간대 오프셋을 보내면 백엔드마다 다르게 해석될 수 있어 스키마가
    `Z`를 강제한다. `datetime.isoformat()`은 `+00:00`을 내므로 치환한다.
    """
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


class MessageMapper:
    """봉투를 만들고 `sequence`를 관리한다.

    `sequence`는 발행자 안에서만 증가하는 번호다. 노드를 재시작하면 0부터 다시
    시작하므로 전역 순서로 쓸 수 없다. 31-5가 "같은 발행자 내 순서 확인용"으로
    한정한 이유다. 중복 판정은 `messageId`로 한다.
    """

    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id
        self._sequence = 0

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    def envelope(
        self,
        message_type: str,
        data: dict[str, Any],
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "messageId": str(uuid.uuid4()),
            "messageType": message_type,
            "robotId": self.robot_id,
            "missionId": mission_id,
            "sequence": self._next_sequence(),
            "sentAt": utc_now_iso(),
            "data": data,
        }

    # ------------------------------------------------------------------
    # presence
    # ------------------------------------------------------------------

    def presence(self, status: str, reason: str | None = None) -> dict[str, Any]:
        return self.envelope(
            MESSAGE_TYPE_PRESENCE,
            {"robotId": self.robot_id, "status": status, "reason": reason},
        )

    def presence_online(self) -> dict[str, Any]:
        return self.presence("ONLINE", None)

    def presence_offline_shutdown(self) -> dict[str, Any]:
        """정상 종료 시 젯슨이 직접 보내는 OFFLINE.

        비정상 종료는 브로커가 LWT로 대신 보낸다. LWT 본문은 접속 시점에
        고정해야 하므로 `mqtt_client`가 따로 만든다.
        """
        return self.presence("OFFLINE", "SHUTDOWN")

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    def state(
        self,
        mission_state: str | None,
        control_mode: str | None,
        safety_state: str | None,
        active_mission_id: str | None = None,
        components: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        return self.envelope(
            MESSAGE_TYPE_STATE,
            {
                "robotId": self.robot_id,
                "missionState": mission_state,
                "controlMode": control_mode,
                "safetyState": safety_state,
                "activeMissionId": active_mission_id,
                "components": components,
            },
            mission_id=active_mission_id,
        )

    # ------------------------------------------------------------------
    # telemetry
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # events (S15P11A301-140)
    # ------------------------------------------------------------------

    def encounter(self, data: dict[str, Any]) -> dict[str, Any]:
        """`/perception/encounter` 본문을 31-5 봉투에 담는다.

        본문을 그대로 넘긴다. `mission_manager`가 이미
        `common/schemas/encounter.schema.json` 형식으로 만들었으므로 여기서 다시
        조립하면 두 곳이 어긋날 자리가 생긴다.

        `messageType`은 phase와 무관하게 `ENCOUNTER_CONFIRMED`다. 봉투 스키마의
        enum이 그것 하나이고, 백엔드는 본문의 `phase`를 보고 INSERT와 UPDATE를
        가른다(S15P11A301-138의 `EncounterWriter`).

        봉투의 `missionId`는 본문 값을 쓴다. 백엔드가 봉투를 우선하고 없으면 본문을
        보므로 둘을 맞춰 두는 편이 안전하다.
        """
        return self.envelope(
            MESSAGE_TYPE_ENCOUNTER,
            data,
            mission_id=data.get("missionId"),
        )

    def interaction_report(self, data: dict[str, Any]) -> dict[str, Any]:
        """음성 노드의 구조화 보고를 events 채널 봉투에 담는다.

        interactionId는 이 보고 사건 자체의 식별자이므로 messageId로 재사용한다.
        TRANSIENT_LOCAL 재전달이나 bridge 재시작이 같은 보고를 새 MQTT 메시지로
        만들더라도 백엔드의 message_id UNIQUE가 중복 저장을 막는다.
        """

        message = self.envelope(
            MESSAGE_TYPE_INTERACTION_REPORT,
            data,
            mission_id=data.get("missionId"),
        )
        message["messageId"] = data["interactionId"]
        return message

    # ------------------------------------------------------------------
    # acks (S15P11A301-143)
    # ------------------------------------------------------------------

    def command_ack(
        self, data: dict[str, Any], mission_id: str | None = None
    ) -> dict[str, Any]:
        """명령 처리 결과를 31-5 봉투에 담는다.

        본문은 `mission_manager`가 만든 것을 그대로 넘긴다. 수락·거부를 판단하는
        것은 상태 머신이고 bridge 가 다시 판단하면 두 곳이 어긋난다. encounter 를
        그렇게 다루는 것과 같은 원칙이다(26.1 단일 권한).

        `missionId`는 명령이 온 봉투의 값을 되돌려준다. 백엔드는 `commandId`로
        `control_commands` 행을 찾으므로 없어도 동작하지만, 봉투 규약이 있는 값을
        비우면 관제 로그에서 어느 임무의 응답인지 알 수 없다.
        """
        return self.envelope(MESSAGE_TYPE_COMMAND_ACK, data, mission_id=mission_id)

    def telemetry(
        self,
        *,
        pose: dict[str, Any] | None = None,
        motion: dict[str, Any] | None = None,
        battery: dict[str, Any] | None = None,
        environment: dict[str, Any] | None = None,
        compute: dict[str, Any] | None = None,
        health: dict[str, Any] | None = None,
        mission_state: str | None = None,
        mission_id: str | None = None,
    ) -> dict[str, Any]:
        return self.envelope(
            MESSAGE_TYPE_TELEMETRY,
            {
                "pose": pose,
                "motion": motion,
                "battery": battery,
                "environment": environment,
                "compute": compute,
                # health는 스키마가 필수로 요구한다. 셋 다 null이어도 객체는 있어야
                # 백엔드가 필드 존재를 전제로 파싱할 수 있다.
                "health": health
                if health is not None
                else {"mcuConnected": None, "lidarOk": None, "cameraOk": None},
                "missionState": mission_state,
            },
            mission_id=mission_id,
        )

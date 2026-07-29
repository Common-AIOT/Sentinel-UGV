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

import uuid
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"

MESSAGE_TYPE_PRESENCE = "ROBOT_PRESENCE"
MESSAGE_TYPE_STATE = "ROBOT_STATE"
MESSAGE_TYPE_TELEMETRY = "ROBOT_TELEMETRY"

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

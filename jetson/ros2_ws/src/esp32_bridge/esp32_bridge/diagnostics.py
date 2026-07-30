"""HELLO_ACK/DIAGNOSTIC 프레임을 diagnostic_msgs/DiagnosticArray로 변환한다.

두 ESP32의 상태·fault·진단 카운터를 /diagnostics로 통일해서 보고하기 위한
헬퍼. diagnostic_msgs 메시지 타입에 의존하므로 ROS 워크스페이스가 source된
환경에서만 import된다(packet_codec.py와 달리 순수 rclpy-free 모듈은 아니다).
"""

from __future__ import annotations

from typing import Optional

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

from .protocol_constants import FAULT_NAMES

BOARD_ROLE_NAMES = {1: "MOTOR", 2: "SENSOR"}


def _fault_key_values(fault_flags: int) -> list:
    return [
        KeyValue(key=name, value=("1" if fault_flags & bit else "0"))
        for bit, name in FAULT_NAMES.items()
    ]


def build_status(
    *,
    hardware_id: str,
    board_role: int,
    board_state_name: str,
    fault_flags: int,
    crc_error_count: int,
    dropped_frame_count: int,
    stale_sequence_count: int,
) -> DiagnosticStatus:
    level = DiagnosticStatus.OK if fault_flags == 0 else DiagnosticStatus.ERROR
    return DiagnosticStatus(
        level=level,
        name=f"esp32_bridge: {BOARD_ROLE_NAMES.get(board_role, 'UNKNOWN')}",
        message=board_state_name,
        hardware_id=hardware_id,
        values=[
            KeyValue(key="board_state", value=board_state_name),
            KeyValue(key="crc_error_count", value=str(crc_error_count)),
            KeyValue(key="dropped_frame_count", value=str(dropped_frame_count)),
            KeyValue(key="stale_sequence_count", value=str(stale_sequence_count)),
            *_fault_key_values(fault_flags),
        ],
    )


def build_array(stamp, statuses: list) -> DiagnosticArray:
    array = DiagnosticArray()
    array.header.stamp = stamp
    array.status = statuses
    return array


class RebootDetector:
    """senderUptimeMs가 이전 값보다 작아지면 보드 재부팅으로 간주한다."""

    def __init__(self) -> None:
        self._last_uptime_ms: Optional[int] = None

    def observe(self, sender_uptime_ms: int) -> bool:
        rebooted = self._last_uptime_ms is not None and sender_uptime_ms < self._last_uptime_ms
        self._last_uptime_ms = sender_uptime_ms
        return rebooted

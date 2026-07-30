"""packet_codec의 CRC16/COBS/프레임 계약 시험 (S15P11A301-84).

rclpy를 import하지 않으므로 브로커/ROS 없이 돈다(sentinel_bridge의
message_mapper/mqtt_client 테스트와 같은 패턴이라 CI에서도 실행할 수 있다).
아래 벡터는 hardware/esp32/jetson-comm/test/test_protocol.cpp, test_vectors/의
값과 동일하다 - 한쪽을 바꾸면 반드시 다른 쪽도 함께 확인할 것.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.packet_codec import (  # noqa: E402
    CobsError,
    CrcError,
    DriveCommand,
    ParsedFrame,
    build_frame,
    cobs_decode,
    cobs_encode,
    crc16_ccitt_false,
    is_sequence_newer,
    pack_drive_command,
    parse_frame,
    unpack_drive_command,
)
from esp32_bridge.protocol_constants import MSG_DRIVE_COMMAND  # noqa: E402


def test_crc16_of_empty_input_is_init_value():
    assert crc16_ccitt_false(b"") == 0xFFFF


def test_crc16_standard_check_value():
    assert crc16_ccitt_false(b"123456789") == 0x29B1


@pytest.mark.parametrize(
    ("raw_hex", "encoded_hex"),
    [
        ("", "01"),
        ("11", "0211"),
        ("1122", "031122"),
        ("110022", "02110222"),
        ("0000", "010101"),
    ],
)
def test_cobs_round_trip(raw_hex, encoded_hex):
    raw = bytes.fromhex(raw_hex)
    encoded = bytes.fromhex(encoded_hex)
    assert cobs_encode(raw) == encoded
    assert cobs_decode(encoded) == raw


def test_cobs_decode_rejects_malformed_input():
    with pytest.raises(CobsError):
        cobs_decode(b"\x00")  # code 바이트가 0이면 형식 오류


def test_build_and_parse_frame_round_trip():
    cmd = DriveCommand(
        mode=2,
        flags=0x01,
        target_drive_left_mmps=-1200,
        target_drive_right_mmps=1500,
        target_steering_mdeg=3200,
        max_accel_mmps2=800,
        max_steering_rate_mdps=4000,
        command_timeout_ms=300,
    )
    payload = pack_drive_command(cmd)
    frame = build_frame(MSG_DRIVE_COMMAND, sequence=42, sender_uptime_ms=123456, payload=payload)

    assert frame.endswith(b"\x00")
    parsed = parse_frame(frame[:-1])
    assert isinstance(parsed, ParsedFrame)
    assert parsed.message_type == MSG_DRIVE_COMMAND
    assert parsed.sequence == 42
    assert parsed.sender_uptime_ms == 123456

    decoded = unpack_drive_command(parsed.payload)
    assert decoded == cmd


def test_parse_frame_rejects_corrupted_crc():
    payload = pack_drive_command(DriveCommand(0, 0, 0, 0, 0, 0, 0, 300))
    frame = bytearray(build_frame(MSG_DRIVE_COMMAND, 1, 0, payload))
    # crc16 바로 앞 payload 바이트 하나를 손상시킨다(마지막 바이트는 0x00 구분자).
    frame[-3] ^= 0xFF
    with pytest.raises((CrcError, CobsError)):
        parse_frame(bytes(frame[:-1]))


@pytest.mark.parametrize(
    ("candidate", "last", "expected"),
    [
        (5, 4, True),
        (4, 5, False),
        (0, 65535, True),
        (5, 5, False),
    ],
)
def test_sequence_wraparound(candidate, last, expected):
    assert is_sequence_newer(candidate, last) == expected

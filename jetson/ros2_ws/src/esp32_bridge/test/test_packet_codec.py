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
    ImuState,
    LengthError,
    ParsedFrame,
    SetMode,
    build_frame,
    cobs_decode,
    cobs_encode,
    crc16_ccitt_false,
    is_sequence_newer,
    pack_drive_command,
    pack_imu_state,
    pack_set_mode,
    parse_frame,
    unpack_drive_command,
    unpack_imu_state,
    unpack_set_mode,
)
from esp32_bridge.protocol_constants import (  # noqa: E402
    ACK_RESULT_REJECTED_STATE,
    BOARD_MODE_AUTO,
    BOARD_MODE_MANUAL,
    BOARD_MODE_VALUES,
    IMU_STATUS_VALID,
    MSG_DRIVE_COMMAND,
    MSG_IMU_STATE,
    MSG_SET_MODE,
    ack_result_name,
    imu_status_flag_names,
    message_type_name,
)


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


# ---- IMU_STATE (0x26) ----
# 아래 값은 hardware/esp32/jetson-comm/test/test_protocol.cpp의
# testImuStateRoundTrip()과 동일하다. 한쪽을 바꾸면 반드시 다른 쪽도 확인할 것.

_IMU_VECTOR = ImuState(
    sample_time_us=0x0000_0123_4567_89AB,  # u32로 잘리면 상위 바이트가 사라진다
    gyro_x_radps=-0.125,
    gyro_y_radps=0.0,
    gyro_z_radps=3.14159,
    accel_x_mps2=9.80665,
    accel_y_mps2=-1.5,
    accel_z_mps2=0.25,
    temperature_centi_c=3125,
    status_flags=IMU_STATUS_VALID,
)


def test_imu_state_payload_is_36_bytes():
    assert len(pack_imu_state(_IMU_VECTOR)) == 36


def test_imu_state_gyro_x_bit_pattern_is_ieee754_little_endian():
    # -0.125f = 0xBE000000. C++ writeF32LE가 쓴 바이트열과 같아야 한다.
    payload = pack_imu_state(_IMU_VECTOR)
    assert payload[8:12] == bytes.fromhex("000000BE")


def test_imu_state_frame_round_trip_preserves_u64_high_bytes():
    payload = pack_imu_state(_IMU_VECTOR)
    frame = build_frame(MSG_IMU_STATE, sequence=7, sender_uptime_ms=999, payload=payload)

    parsed = parse_frame(frame[:-1])
    assert parsed.message_type == MSG_IMU_STATE  # 0x26이 KNOWN_MESSAGE_TYPES에 있다

    decoded = unpack_imu_state(parsed.payload)
    assert decoded.sample_time_us == _IMU_VECTOR.sample_time_us
    assert decoded.temperature_centi_c == 3125
    assert decoded.status_flags == IMU_STATUS_VALID
    # f32로 왕복하므로 완전 일치를 요구하지 않는다(비트 패턴은 위 시험이 본다).
    assert decoded.gyro_x_radps == pytest.approx(-0.125)
    assert decoded.gyro_z_radps == pytest.approx(3.14159, rel=1e-6)
    assert decoded.accel_x_mps2 == pytest.approx(9.80665, rel=1e-6)
    assert decoded.accel_y_mps2 == pytest.approx(-1.5)


def test_unpack_imu_state_rejects_short_payload():
    # C++ unpackImuState가 false를 내는 것과 같은 취급. struct.error가 아니라
    # ProtocolError 계열이어야 브리지 수신 루프가 잡아낼 수 있다.
    payload = pack_imu_state(_IMU_VECTOR)
    with pytest.raises(LengthError):
        unpack_imu_state(payload[:-1])


def test_imu_status_flag_names_reports_unknown_bits():
    assert imu_status_flag_names(IMU_STATUS_VALID) == ["VALID"]
    assert imu_status_flag_names(0) == []
    assert imu_status_flag_names(0x0010) == ["UNKNOWN(0x0010)"]


# ---- SET_MODE (0x13) ----
# 이 페이로드는 C++ packSetMode와 바이트 단위로 같아야 한다
# (hardware/esp32/jetson-comm/test/test_protocol.cpp testSetModeRoundTrip).


def test_set_mode_payload_is_two_bytes():
    assert len(pack_set_mode(SetMode(BOARD_MODE_AUTO, 0))) == 2


def test_set_mode_byte_vector_matches_cpp():
    # C++ 쪽이 못박는 것과 같은 벡터. 한쪽 struct 포맷을 바꾸면 여기가 먼저 깨진다.
    assert pack_set_mode(SetMode(BOARD_MODE_AUTO, 0)) == bytes.fromhex("0200")
    assert pack_set_mode(SetMode(BOARD_MODE_MANUAL, 0)) == bytes.fromhex("0100")


def test_set_mode_frame_round_trip():
    payload = pack_set_mode(SetMode(BOARD_MODE_MANUAL, 0))
    frame = build_frame(MSG_SET_MODE, sequence=11, sender_uptime_ms=555, payload=payload)

    # 0x13이 KNOWN_MESSAGE_TYPES에 없으면 UnknownMessageTypeError가 난다. 펌웨어에서는
    # 같은 누락이 droppedFrameCount만 조용히 올린다(계획 리스크 3).
    parsed = parse_frame(frame[:-1])
    assert parsed.message_type == MSG_SET_MODE

    decoded = unpack_set_mode(parsed.payload)
    assert decoded.requested_mode == BOARD_MODE_MANUAL
    assert decoded.flags == 0


@pytest.mark.parametrize("bad", [b"", b"\x01", b"\x01\x00\x00"])
def test_unpack_set_mode_rejects_wrong_length(bad):
    with pytest.raises(LengthError):
        unpack_set_mode(bad)


def test_board_mode_values_has_exactly_manual_and_auto():
    # SET_MODE(SAFE_IDLE)은 보내는 쪽이 없으므로 0을 정의하지 않는다(계획 D11).
    assert BOARD_MODE_VALUES == {"MANUAL": 1, "AUTO": 2}


def test_name_lookups_label_known_values_and_flag_unknown_ones():
    assert message_type_name(MSG_SET_MODE) == "SET_MODE"
    assert ack_result_name(ACK_RESULT_REJECTED_STATE) == "REJECTED_STATE"
    assert message_type_name(0x99) == "UNKNOWN(153)"
    assert ack_result_name(9) == "UNKNOWN(9)"


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

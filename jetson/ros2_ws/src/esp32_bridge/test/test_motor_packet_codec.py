"""motor_packet_codec의 동기워드+고정길이+CRC8 프레이밍 계약 시험 (S15P11A301-321).

rclpy를 import하지 않으므로 브로커/ROS 없이 돈다(sentinel_bridge의
message_mapper/mqtt_client 테스트, 그리고 이 패키지의 test_packet_codec.py와
같은 패턴이라 CI에서도 실행할 수 있다).

CRC-8 손계산 벡터와 프레임 상수는
`hardware/esp32/motor/esp32_motor_comm/motor_protocol.h`/`test/test_motor_protocol.cpp`
와 동일한 값이다 - 한쪽을 바꾸면 반드시 다른 쪽도 함께 확인할 것.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.motor_packet_codec import (
    DriveState,
    pack_drive_state,
    unpack_drive_state,  # noqa: E402
    CrcError,
    DriveCommand,
    LengthError,
    MotorDiagnostic,
    SetMode,
    SyncError,
    build_motor_frame,
    crc8,
    is_motor_sequence_newer,
    pack_drive_command,
    pack_motor_diagnostic,
    pack_set_mode,
    parse_motor_frame,
    unpack_drive_command,
    unpack_motor_diagnostic,
    unpack_set_mode,
)
from esp32_bridge.motor_protocol_constants import (
    AUTHORITY_FLAG_MANUAL_FALLBACK,  # noqa: E402
    BOARD_MODE_AUTO,
    BOARD_MODE_MANUAL,
    BOARD_ROLE_MOTOR,
    FRAME_BYTES,
    MSG_DRIVE_COMMAND,
    MSG_HELLO,
    MSG_SET_MODE,
    PAYLOAD_BYTES,
    SYNC0,
    SYNC1,
)


# ---- CRC-8: 손계산으로 검증한 벡터 (motor_protocol.h 헤더 주석 참고) ----


def test_crc8_of_empty_input_is_init_value():
    assert crc8(b"") == 0x00


def test_crc8_hand_verified_vectors():
    # poly=0x07, init=0x00. 단일 비트 1개짜리 바이트는 그 비트가 최상위까지 밀려
    # 올라가는 시프트 횟수만큼만 다항식이 섞이므로 손으로 추적 가능하다.
    assert crc8(bytes([0x01])) == 0x07
    assert crc8(bytes([0x02])) == 0x0E


def test_frame_constants():
    assert PAYLOAD_BYTES == 22
    assert FRAME_BYTES == 27


def test_build_frame_has_sync_word_and_fixed_length():
    frame = build_motor_frame(MSG_HELLO, sequence=7)
    assert len(frame) == FRAME_BYTES
    assert frame[0] == SYNC0
    assert frame[1] == SYNC1


def test_build_and_parse_frame_round_trip_hello():
    frame = build_motor_frame(MSG_HELLO, sequence=7)
    parsed = parse_motor_frame(frame)
    assert parsed.message_type == MSG_HELLO
    assert parsed.sequence == 7
    assert parsed.payload == bytes(PAYLOAD_BYTES)  # 0-length payload는 전부 0-패딩


def test_set_mode_byte_vector_matches_cpp():
    # C++ 쪽이 못박는 것과 같은 벡터. 한쪽 struct 포맷을 바꾸면 여기가 먼저 깨진다.
    assert pack_set_mode(SetMode(BOARD_MODE_AUTO, 0)) == bytes.fromhex("0200")
    assert pack_set_mode(SetMode(BOARD_MODE_MANUAL, 0)) == bytes.fromhex("0100")


def test_set_mode_frame_round_trip():
    payload = pack_set_mode(SetMode(BOARD_MODE_MANUAL, 0))
    frame = build_motor_frame(MSG_SET_MODE, sequence=11, payload=payload)

    parsed = parse_motor_frame(frame)
    assert parsed.message_type == MSG_SET_MODE
    assert parsed.sequence == 11

    decoded = unpack_set_mode(parsed.payload)
    assert decoded.requested_mode == BOARD_MODE_MANUAL
    assert decoded.flags == 0


def test_drive_command_round_trip_through_padded_payload():
    cmd = DriveCommand(
        mode=2,
        flags=0,
        target_drive_left_mmps=250,
        target_drive_right_mmps=-250,
        target_steering_mdeg=12345,
        max_accel_mmps2=500,
        max_steering_rate_mdps=60000,
        command_timeout_ms=300,
    )
    frame = build_motor_frame(MSG_DRIVE_COMMAND, sequence=42, payload=pack_drive_command(cmd))
    parsed = parse_motor_frame(frame)
    assert parsed.message_type == MSG_DRIVE_COMMAND
    assert parsed.sequence == 42
    assert len(parsed.payload) == PAYLOAD_BYTES  # 14바이트 실데이터 + 패딩

    decoded = unpack_drive_command(parsed.payload)
    assert decoded == cmd


def test_motor_diagnostic_round_trip_includes_link_silence_ms():
    diag = MotorDiagnostic(
        board_role=BOARD_ROLE_MOTOR,
        board_state=4,
        fault_flags=0x0021,
        crc_error_count=3,
        dropped_frame_count=5,
        stale_sequence_count=7,
        free_heap_bytes=123456,
        link_silence_ms=42,
    )
    payload = pack_motor_diagnostic(diag)
    assert len(payload) == PAYLOAD_BYTES  # DIAGNOSTIC이 가장 커서 패딩이 없다

    decoded = unpack_motor_diagnostic(payload)
    assert decoded == diag


def test_parse_frame_rejects_wrong_length():
    frame = build_motor_frame(MSG_HELLO, sequence=1)
    with pytest.raises(LengthError):
        parse_motor_frame(frame[:-1])


def test_parse_frame_rejects_bad_sync_word():
    frame = bytearray(build_motor_frame(MSG_HELLO, sequence=1))
    frame[0] = 0x00
    with pytest.raises(SyncError):
        parse_motor_frame(bytes(frame))


def test_parse_frame_rejects_corrupted_crc():
    frame = bytearray(build_motor_frame(MSG_DRIVE_COMMAND, sequence=1, payload=pack_drive_command(
        DriveCommand(0, 0, 0, 0, 0, 0, 0, 0)
    )))
    frame[10] ^= 0xFF  # payload 영역 한 바이트를 뒤집는다
    with pytest.raises(CrcError):
        parse_motor_frame(bytes(frame))


def test_build_frame_rejects_oversized_payload():
    with pytest.raises(ValueError):
        build_motor_frame(MSG_HELLO, sequence=1, payload=bytes(PAYLOAD_BYTES + 1))


@pytest.mark.parametrize(
    ("candidate", "last", "expected"),
    [
        (1, 0, True),
        (0, 0, False),
        (0, 255, True),   # 랩어라운드
        (255, 0, False),  # 랩어라운드 반대 방향은 "더 새 값"이 아니다
    ],
)
def test_sequence_wraparound(candidate, last, expected):
    assert is_motor_sequence_newer(candidate, last) == expected


def test_resync_after_garbage_recovers_next_frame():
    """comm_task.cpp의 feedByte()/motor_serial_transport.py의 _pump()와 같은
    슬라이딩 윈도우를 여기서 흉내 내, 가비지 뒤에 온 진짜 프레임이 복구되는지
    확인한다."""
    real_frame = build_motor_frame(MSG_HELLO, sequence=99)
    garbage = bytes([0x11, 0x22, 0x33, 0x44, 0x55])
    stream = garbage + real_frame

    window = bytearray()
    dispatched = None
    for byte in stream:
        if len(window) < FRAME_BYTES:
            window.append(byte)
        else:
            del window[0]
            window.append(byte)
        if len(window) < FRAME_BYTES:
            continue
        if window[0] == SYNC0 and window[1] == SYNC1:
            try:
                dispatched = parse_motor_frame(bytes(window))
                window.clear()
                break
            except (SyncError, CrcError):
                pass

    assert dispatched is not None
    assert dispatched.message_type == MSG_HELLO
    assert dispatched.sequence == 99


# ---- DRIVE_STATE 길이 호환 (S15P11A301-345) ----------------------------------
#
# 모터 보드와 젯슨은 **따로 배포된다.** 2026-08-09 에 센서 보드만 플래시된 상태가
# 실제로 있었다. 그래서 15바이트(구판)와 16바이트(345) 어느 쪽이 와도 파싱이
# 깨지지 않아야 하고, 그것을 여기서 고정한다 — 16바이트를 강제하면 구판 보드에서
# 모터 링크가 통째로 죽는다.


def _sample_drive_state() -> DriveState:
    return DriveState(
        applied_sequence=100,
        state=4,
        fault_flags=0,
        drive_pwm_left_permille=160,
        drive_pwm_right_permille=160,
        target_steering_mdeg=0,
        steering_actuator_cmd=1574,
        estop_active=0,
        driver_enabled=1,
    )


def test_drive_state_accepts_legacy_15_byte_payload():
    payload = pack_drive_state(_sample_drive_state())
    assert len(payload) == 15

    parsed = unpack_drive_state(payload)

    assert parsed.drive_pwm_left_permille == 160
    # 구판은 폴백 여부를 말하지 않는다. 「모름」을 참으로 읽으면 관제가 정상
    # 수동을 폴백으로 표시한다.
    assert parsed.authority_flags == 0


def test_drive_state_reads_authority_flags_from_16_byte_payload():
    payload = pack_drive_state(_sample_drive_state()) + bytes(
        [AUTHORITY_FLAG_MANUAL_FALLBACK]
    )

    parsed = unpack_drive_state(payload)

    assert parsed.authority_flags & AUTHORITY_FLAG_MANUAL_FALLBACK
    # 앞 15바이트 해석이 밀리지 않아야 한다.
    assert parsed.applied_sequence == 100
    assert parsed.steering_actuator_cmd == 1574


def test_drive_state_ignores_frame_padding_beyond_authority_flags():
    # 실제 프레임은 PAYLOAD_BYTES(22)로 0 패딩돼 온다. 패딩을 플래그로 읽으면
    # 폴백이 아닌데 폴백으로 보고된다.
    payload = pack_drive_state(_sample_drive_state()) + bytes([0]) + bytes(6)

    parsed = unpack_drive_state(payload)

    assert parsed.authority_flags == 0

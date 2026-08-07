"""ROS 없이 포트를 열어 HELLO를 보내고 HELLO_ACK를 출력하는 모터 전용 브링업 도구
(S15P11A301-321).

`hello_check.py`와 같은 목적이지만 모터 링크의 새 프레이밍(동기워드+고정길이+CRC8,
`motor_packet_codec`)을 쓴다 - 옛 `hello_check.py`(COBS+CRC16, `packet_codec`)로
새로 플래싱한 모터 보드를 확인하면 프레임 자체를 못 알아봐 계속 타임아웃만 난다.
센서 보드 확인에는 여전히 `hello_check.py`를 쓴다.

    ros2 run esp32_bridge esp32_motor_hello_check --port /dev/ttyUSB0
    python3 -m esp32_bridge.tools.motor_hello_check --port /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import time
from typing import Optional, Sequence

from ..motor_packet_codec import (
    CrcError,
    LengthError,
    SyncError,
    build_motor_frame,
    parse_motor_frame,
    unpack_hello_ack,
)
from ..motor_protocol_constants import BOARD_ROLE_MOTOR, MOTOR_PROTOCOL_VERSION, MSG_HELLO, MSG_HELLO_ACK
from ..motor_serial_transport import MotorSerialTransport

_FRAME_PARSE_ERRORS = (SyncError, LengthError, CrcError)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="예: /dev/ttyUSB0 또는 /dev/sentinel_mcu_motor")
    parser.add_argument("--baudrate", type=int, default=921600)
    parser.add_argument(
        "--timeout",
        type=float,
        default=6.0,
        help="HELLO_ACK 대기 시간(초). ESP32 auto-reset 안정화 대기(약 1.5초)를 포함해 넉넉히 잡는다",
    )
    parser.add_argument("--hello-interval", type=float, default=0.5, help="HELLO 재전송 간격(초)")
    args = parser.parse_args(argv)

    transport = MotorSerialTransport(args.port, args.baudrate)
    transport.open()
    print(f"{args.port} @ {args.baudrate}bps 연결 중 (ESP32 재부팅 안정화 대기 포함, 새 모터 프레이밍)...")

    # 포트가 열리자마자 한 번만 HELLO를 보내면, 안정화 대기(보드가 아직 안 붙어
    # 있음) 동안 전송이 조용히 실패하고 끝나버릴 수 있다. 안정화가 끝나 실제로
    # 연결될 때까지 주기적으로 재시도한다.
    sequence = 1
    next_hello_at = 0.0
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_hello_at:
            frame = build_motor_frame(MSG_HELLO, sequence=sequence)
            try:
                transport.write_frame(frame)
                print(f"HELLO 전송(seq={sequence})")
            except Exception:  # noqa: BLE001 - 아직 안정화 중일 수 있다, 다음 주기에 재시도
                pass
            sequence = (sequence + 1) & 0xFF
            next_hello_at = now + args.hello_interval

        raw = transport.read_frame(timeout=0.2)
        if raw is None:
            continue
        try:
            parsed = parse_motor_frame(raw)
        except _FRAME_PARSE_ERRORS as exc:
            print(f"프레임 파싱 실패(무시): {exc}")
            continue
        if parsed.message_type != MSG_HELLO_ACK:
            continue

        ack = unpack_hello_ack(parsed.payload)
        role_name = "MOTOR" if ack.board_role == BOARD_ROLE_MOTOR else f"UNKNOWN({ack.board_role})"
        version_note = "" if ack.protocol_version == MOTOR_PROTOCOL_VERSION else (
            f" (경고: Jetson 기대값={MOTOR_PROTOCOL_VERSION}과 불일치 - 펌웨어/브리지 버전이 안 맞을 수 있다)"
        )
        print(
            f"HELLO_ACK 수신: role={role_name} "
            f"fw={ack.firmware_major}.{ack.firmware_minor}.{ack.firmware_patch} "
            f"protocol_version={ack.protocol_version}{version_note} board_state={ack.board_state} "
            f"fault_flags=0x{ack.fault_flags:04x}"
        )
        transport.close()
        return 0

    print(f"{args.timeout}초 내 HELLO_ACK를 받지 못했습니다. 배선/포트/보드 플래싱을 확인하세요.")
    transport.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

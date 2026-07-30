"""ROS 없이 포트를 열어 HELLO를 보내고 HELLO_ACK를 출력하는 브링업 도구.

ROS 토픽/노드를 전혀 쓰지 않으므로 esp32_bridge 패키지를 colcon build하기 전,
또는 배선/플래싱만 막 끝낸 상태에서 가장 먼저 돌려볼 수 있는 확인 수단이다.

    ros2 run esp32_bridge esp32_hello_check --port /dev/ttyUSB0
    python3 -m esp32_bridge.tools.hello_check --port /dev/ttyUSB0
"""

from __future__ import annotations

import argparse
import time
from typing import Optional, Sequence

from ..packet_codec import (
    CobsError,
    CrcError,
    LengthError,
    UnknownMessageTypeError,
    VersionError,
    build_frame,
    parse_frame,
    unpack_hello_ack,
)
from ..protocol_constants import BOARD_ROLE_MOTOR, BOARD_ROLE_SENSOR, MSG_HELLO, MSG_HELLO_ACK
from ..serial_transport import SerialTransport

_FRAME_PARSE_ERRORS = (CobsError, LengthError, VersionError, UnknownMessageTypeError, CrcError)
_ROLE_NAMES = {BOARD_ROLE_MOTOR: "MOTOR", BOARD_ROLE_SENSOR: "SENSOR"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="예: /dev/ttyUSB0 또는 /dev/sentinel_mcu_motor")
    parser.add_argument("--baudrate", type=int, default=921600)
    parser.add_argument(
        "--timeout",
        type=float,
        default=6.0,
        help="HELLO_ACK 대기 시간(초). ESP32 auto-reset 안정화 대기(SerialTransport, 약 1.5초)를"
        " 포함해 넉넉히 잡는다",
    )
    parser.add_argument("--hello-interval", type=float, default=0.5, help="HELLO 재전송 간격(초)")
    args = parser.parse_args(argv)

    transport = SerialTransport(args.port, args.baudrate)
    transport.open()
    print(f"{args.port} @ {args.baudrate}bps 연결 중 (ESP32 재부팅 안정화 대기 포함)...")

    # 포트가 열리자마자 한 번만 HELLO를 보내면, SerialTransport가 auto-reset 안정화를
    # 위해 대기하는 동안(보드가 아직 안 붙어 있음) 전송이 조용히 실패하고 끝나버릴 수
    # 있다. 안정화가 끝나 실제로 연결될 때까지 주기적으로 재시도한다.
    sequence = 1
    next_hello_at = 0.0
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_hello_at:
            frame = build_frame(MSG_HELLO, sequence=sequence, sender_uptime_ms=0, payload=b"")
            try:
                transport.write_frame(frame)
                print(f"HELLO 전송(seq={sequence})")
            except Exception:  # noqa: BLE001 - 아직 안정화 중일 수 있다, 다음 주기에 재시도
                pass
            sequence = (sequence + 1) & 0xFFFF
            next_hello_at = now + args.hello_interval

        raw = transport.read_frame(timeout=0.2)
        if raw is None:
            continue
        try:
            parsed = parse_frame(raw)
        except _FRAME_PARSE_ERRORS as exc:
            print(f"프레임 파싱 실패(무시): {exc}")
            continue
        if parsed.message_type != MSG_HELLO_ACK:
            continue

        ack = unpack_hello_ack(parsed.payload)
        role_name = _ROLE_NAMES.get(ack.board_role, f"UNKNOWN({ack.board_role})")
        print(
            f"HELLO_ACK 수신: role={role_name} "
            f"fw={ack.firmware_major}.{ack.firmware_minor}.{ack.firmware_patch} "
            f"protocol_version={ack.protocol_version} board_state={ack.board_state} "
            f"fault_flags=0x{ack.fault_flags:04x}"
        )
        transport.close()
        return 0

    print(f"{args.timeout}초 내 HELLO_ACK를 받지 못했습니다. 배선/포트/보드 플래싱을 확인하세요.")
    transport.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""브리지 링크 판정 (S15P11A301-323).

**「보드가 말이 없다」와 「말은 하는데 우리가 버린다」를 가른다.** 두 실패가 ROS
에서 같아 보였고, 그 때문에 2026-08-06~07 진단에서 왕복을 여러 번 했다.

- 모터 보드 무응답(S15P11A301-317): `/diagnostics` 에 MOTOR 항목이 **아예 없었다**
  — 이 노드가 보드의 DIAGNOSTIC 프레임을 받았을 때만 발행했기 때문이다. 「항목
  없음」은 화면에서 「정상」과 구별되지 않는다. 게다가 파싱 실패를 카운터 없이
  버려서, 「바이트가 안 온다」와 「보레이트가 어긋나 쓰레기가 온다」가 같아 보였다.
- 환경 프레임(S15P11A301-322 조사 중): `/environment/*` 가 조용한 이유가 미수신인지
  `status_flags != VALID` 로 버린 것인지 알 수 없어, **정상 동작이 「펌웨어가 소스와
  다르다」로 오진**됐다.

판정을 여기 두는 이유는 `recording_health.py` 와 같다 — 노드 파일은 `rclpy` 를
import 해 CI 에서 돌지 않는다. 여기서 틀리면 화면이 조용히 거짓말을 하므로 시험이
붙는 자리에 둔다. 그래서 이 모듈은 ROS 타입을 쓰지 않고 `level` 을 문자열로 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# diagnostic_msgs/DiagnosticStatus 의 값과 같은 이름. 노드가 매핑한다.
OK = 'OK'
WARN = 'WARN'
ERROR = 'ERROR'


@dataclass(frozen=True)
class Verdict:
    """진단 한 줄. `values` 는 사람이 원인을 좁히는 데 쓰는 근거다."""

    level: str
    message: str
    values: dict[str, str] = field(default_factory=dict)


def motor_link_verdict(
    *,
    handshake_ok: bool,
    rx_frame_count: int,
    parse_error_count: int,
    parse_errors_by_type: dict[str, int] | None = None,
    hello_sent_count: int,
    since_last_rx_s: float | None,
    rx_silent_after_s: float = 2.0,
) -> Verdict:
    """모터 링크 상태.

    순서가 중요하다 — **먼저 「무엇이 오는가」를 답하고 그다음 「쓸 수 있는가」를
    답한다.** 오늘 겪은 실패는 전자에서 갈렸는데 종전 진단은 후자만 말했다.

    `rx_frame_count` 는 **COBS 구분자로 프레임이 선 횟수**이고 파싱 성공 여부와
    무관하다. 그래서 0 이면 바이트가 없거나 구분자가 없는 것이고, 0 이 아닌데
    파싱 실패만 쌓이면 보레이트·프로토콜 버전·역할 불일치 쪽이다. 이 구분이
    이 판정의 목적 전부다.
    """
    values = {
        'handshake_ok': str(handshake_ok),
        'rx_frame_count': str(rx_frame_count),
        'parse_error_count': str(parse_error_count),
        'hello_sent_count': str(hello_sent_count),
        'since_last_rx_s': '-' if since_last_rx_s is None else f'{since_last_rx_s:.1f}',
    }
    for name, count in sorted((parse_errors_by_type or {}).items()):
        values[f'parse_error.{name}'] = str(count)

    if rx_frame_count == 0:
        return Verdict(
            ERROR,
            '보드 무응답 — 프레임을 한 번도 받지 못했다. '
            '전원·펌웨어·USB 배선을 확인하라(수신 바이트가 없다)',
            values,
        )

    if not handshake_ok and parse_error_count > 0:
        # 프레임은 서는데 해석이 안 된다. 보레이트가 어긋나면 0x00 이 우연히 섞여
        # 이 모양이 된다 — 「무응답」과 헷갈리지 않게 따로 말한다.
        return Verdict(
            ERROR,
            '프레임은 오는데 해석하지 못한다 — 보레이트·프로토콜 버전·보드 역할을 확인하라',
            values,
        )

    if not handshake_ok:
        return Verdict(
            ERROR,
            '핸드셰이크 미완 — HELLO 에 HELLO_ACK 이 오지 않았다',
            values,
        )

    if since_last_rx_s is not None and since_last_rx_s > rx_silent_after_s:
        return Verdict(
            WARN,
            f'{since_last_rx_s:.1f}s 동안 수신 없음 — 링크가 끊겼을 수 있다',
            values,
        )

    if parse_error_count > 0:
        # 회복 가능한 잡음이다. 무시하지 않고 보이게만 둔다.
        return Verdict(WARN, f'프레임 {parse_error_count}건을 해석하지 못했다', values)

    return Verdict(OK, '정상', values)


def environment_verdict(
    *,
    received_count: int,
    published_count: int,
    dropped_by_flag: dict[int, int] | None = None,
    last_status_flags: int | None,
    valid_flags: int = 0,
) -> Verdict:
    """환경(DHT) 프레임 상태. IMU 판정과 같은 모양이다.

    브리지는 `status_flags != VALID` 인 프레임을 **버린다** — 보드가 마지막 정상값을
    들고 있어 그대로 발행하면 옛 값이 새 측정처럼 보이기 때문이다. 그 판단은 옳지만,
    버린 사실이 어디에도 안 남아서 「센서가 죽었다」와 「값이 간헐적으로만 유효하다」가
    구별되지 않았다.

    보드의 `ENVIRONMENT_SENSOR_FAULT` 는 **연속 실패**에만 뜨고 성공 한 번이면
    리셋되므로(펌웨어 `sensor_task.cpp`), 간헐 실패는 fault 0 인 채로 값만 사라진다.
    그 조합이 정확히 오진의 원인이었다.
    """
    dropped = dropped_by_flag or {}
    dropped_total = sum(dropped.values())
    values = {
        'received_count': str(received_count),
        'published_count': str(published_count),
        'dropped_count': str(dropped_total),
        'last_status_flags': '-' if last_status_flags is None else f'0x{last_status_flags:02x}',
    }
    for flags, count in sorted(dropped.items()):
        values[f'dropped.0x{flags:02x}'] = str(count)

    if received_count == 0:
        return Verdict(WARN, 'ENVIRONMENT_STATE 프레임 미수신', values)

    if published_count == 0:
        return Verdict(
            ERROR,
            f'수신 {received_count}건 전부 버림 — DHT 가 유효값을 내지 못하고 있다'
            f'(status_flags={values["last_status_flags"]})',
            values,
        )

    if dropped_total > 0:
        ratio = dropped_total / received_count
        return Verdict(
            WARN,
            f'간헐 실패 — 수신 {received_count}건 중 {dropped_total}건 버림 '
            f'({ratio * 100:.0f}%). 연속 3회가 아니면 보드 fault 비트는 뜨지 않는다',
            values,
        )

    if last_status_flags is not None and last_status_flags != valid_flags:
        return Verdict(WARN, f'마지막 프레임이 유효하지 않다({values["last_status_flags"]})', values)

    return Verdict(OK, 'VALID', values)

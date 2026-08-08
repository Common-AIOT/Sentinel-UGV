"""pyserial 기반 시리얼 포트 래퍼.

0x00 구분자로 나뉜 원시 청크(파싱 전 COBS 블록)를 큐에 담아 넘긴다. 프레이밍
문법(COBS/CRC16)은 전혀 모르며, 그 파싱은 호출자가 packet_codec.parse_frame()
으로 한다. 포트가 없거나 끊기면 백그라운드 스레드가 재연결을 계속 시도한다.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import os
import serial

_MAX_ACCUM_BYTES = 256  # MAX_FRAME_BYTES(140)보다 넉넉한 여유

# ESP32 auto-reset 회로가 DTR/RTS로 GPIO0/EN을 제어한다. DTR/RTS를 막 풀어 보드가
# 리셋에서 빠져나오면 그 순간부터 재부팅이 시작되므로, ESP-IDF 부트로더+FreeRTOS
# 태스크 초기화가 끝날 때까지 짧게 대기해야 그 사이의 ROM 부팅 배너(74880bps라
# 우리 baudrate로는 깨진 바이트로 보인다)를 프로토콜 프레임으로 오인하지 않는다.
_BOOT_SETTLE_S = 1.5


def _port_exists(port_name: str) -> bool:
    """장치 노드가 존재하는가 (S15P11A301-340).

    「보드가 안 꽂혔다」와 「남이 그 포트를 쓰고 있다」를 가르는 데 쓴다. 포트가
    존재하는데 열리지 않으면 점유가 유일하게 가능한 원인이고, 그때 안내가
    달라져야 한다 — 2026-08-07 에 두 경우가 같은 문구로 나와 중복 기동 진단이
    늦어졌다. 심링크(udev 별칭)도 따라가도록 os.path.exists 를 쓴다.
    """
    return os.path.exists(port_name)


class SerialNotConnectedError(Exception):
    """포트가 열려 있지 않은 상태에서 write_frame을 호출했을 때."""


class SerialTransport:
    def __init__(
        self,
        port: str,
        baudrate: int = 921600,
        *,
        reconnect_delay_s: float = 1.0,
        logger=None,
    ) -> None:
        self._port_name = port
        self._baudrate = baudrate
        self._reconnect_delay_s = reconnect_delay_s
        self._logger = logger

        self._serial: Optional[serial.Serial] = None
        self._serial_lock = threading.Lock()
        self._rx_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._stop_event = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        # 연결 성공·연속 실패 횟수. 로그 억제와 재열거 추적에 쓴다.
        self._connects = 0
        self._connect_failures = 0

    @property
    def connects(self) -> int:
        """연결 성공 횟수. 1 보다 크면 재연결이 일어났다는 뜻이다."""
        return self._connects

    # 로깅은 심각도별로 나눈다. 한 함수에서 `getattr(logger, level)` 로 보내면
    # 모든 심각도가 **같은 호출 위치**에서 나가고, rclpy 로거는 위치별로 상태를
    # 관리하므로 위치가 같은데 심각도가 바뀌면 거부한다:
    #
    #   ValueError: Logger severity cannot be changed between calls.
    #
    # 이것이 S15P11A301-264 의 원인이었다. 기동 시 info 가 그 위치에서 나간 뒤
    # 첫 warn 이 같은 위치에서 나가려다 터졌고, 예외가 리더 스레드를 죽여
    # **재연결 자체가 사라졌다.** 심각도마다 자기 줄에서 로거를 부르게 한다.
    #
    # try 로 감싸는 것은 이중 방어다. 로깅 실패가 통신 계층을 멈추게 하는 일이
    # 다시 없어야 한다 — 로그는 부수 효과이고 재연결이 본체다.
    def _info(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.info(message)
        except Exception:  # noqa: BLE001 - 로깅 실패로 통신을 멈추지 않는다
            pass

    def _warn(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(message)
        except Exception:  # noqa: BLE001 - 위와 같다
            pass

    def open(self) -> None:
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._run, name=f"serial-rx-{self._port_name}", daemon=True
        )
        self._reader_thread.start()

    def close(self) -> None:
        self._stop_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
        self._disconnect()

    def write_frame(self, frame: bytes) -> None:
        with self._serial_lock:
            if self._serial is None or not self._serial.is_open:
                raise SerialNotConnectedError(f"{self._port_name} is not open")
            try:
                self._serial.write(frame)
                return
            except (serial.SerialException, OSError) as exc:
                # 쓰기 실패도 재연결 신호다. USB 재열거 때 pyserial 은
                # `write failed: [Errno 5] Input/output error` 를 내는데, 이것을
                # 호출자에게만 던지면 아무도 포트를 닫지 않아 죽은 fd 로 영원히
                # 재시도한다(S15P11A301-264 실측: 100초 넘게 회복 안 됨).
                #
                # 리더 스레드도 같은 상황을 감지하지만 이쪽을 따로 두는 이유는
                # **독립성**이다. 한쪽 경로가 막혀도 다른 쪽에서 회복이 시작된다.
                #
                # 예외 변수는 except 블록을 벗어나면 삭제되므로 메시지를 지금 뽑는다.
                reason = str(exc)

        # 락 밖에서 끊는다 — _disconnect 가 같은 락을 잡는다.
        self._disconnect()
        raise SerialNotConnectedError(
            f"{self._port_name} write failed ({reason}), disconnected for reconnect"
        )

    def read_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """수신된 0x00-구분 COBS 블록 하나를 반환한다. timeout 내 없으면 None."""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run(self) -> None:
        """수신 루프 겸 **재연결 담당**. 이 스레드가 죽으면 회복이 사라진다.

        S15P11A301-264 에서 실제로 죽었다 — 재연결 경로의 로그 한 줄이
        `ValueError` 를 냈고 그것이 잡히지 않아 스레드가 종료됐다. 프로세스는
        살아 있어서 아무도 눈치채지 못했고, 그 브리지는 영구히 회복 불가가 됐다.
        그래서 루프 본문 전체를 감싼다 — **어떤 예외도 이 스레드를 멈추게 하지
        않는다.**
        """
        accum = bytearray()
        while not self._stop_event.is_set():
            try:
                self._pump(accum)
            except Exception as exc:  # noqa: BLE001 - 위 docstring 참조
                self._warn(f"{self._port_name} 수신 루프 예외 ({exc}), 계속한다")
                self._disconnect()
                time.sleep(self._reconnect_delay_s)

    def _pump(self, accum: bytearray) -> None:
        """루프 한 번. 예외는 `_run` 이 받는다."""
        with self._serial_lock:
            connected = self._serial is not None and self._serial.is_open
        if not connected:
            if not self._connect():
                time.sleep(self._reconnect_delay_s)
            return

        try:
            byte = self._serial.read(1)
        except (serial.SerialException, OSError) as exc:
            self._warn(f"{self._port_name} read failed ({exc}), reconnecting")
            self._disconnect()
            return

        if not byte:
            return  # read timeout, 정상

        if byte == b"\x00":
            if accum:
                try:
                    self._rx_queue.put_nowait(bytes(accum))
                except queue.Full:
                    self._warn(f"{self._port_name} rx queue full, dropping frame")
                accum.clear()
            return

        accum += byte
        if len(accum) > _MAX_ACCUM_BYTES:
            self._warn(f"{self._port_name} frame too long without delimiter, resetting")
            accum.clear()

    def _connect(self) -> bool:
        try:
            # exclusive=True 로 TIOCEXCL 을 건다 (S15P11A301-340).
            #
            # 이것이 없으면 **두 프로세스가 같은 tty 를 동시에 열 수 있다.**
            # 2026-08-07 에 스택이 두 벌 떠서 브리지 둘이 같은 포트를 열었고,
            # 각자 바이트를 일부만 받아 프레임이 깨지자 링크가 죽은 줄 알고
            # 재연결했다. 그런데 **재연결 한 번이 보드 리셋 한 번**이다 — 아래
            # 주석대로 포트를 여는 순간 DTR/RTS 가 auto-reset 회로를 때린다.
            # 그 결과 센서 보드가 73초 동안 29회 재부팅했고, 재부팅마다 브리지가
            # reset_encoder_origin() 을 불러 그 구간 이동량이 버려져 오도메트리
            # 실측이 통째로 오염됐다(S15P11A301-337·339).
            #
            # 재연결 주기(reconnect_delay 1.0 + BOOT_SETTLE 1.5 = 2.5초)가 관측된
            # 재부팅 주기와 정확히 일치한 것이 이 진단의 근거다.
            #
            # S15P11A301-338 이 demo_up.sh 에서 스택 중복 기동을 막았지만 이쪽이
            # 더 아래층이고 더 넓다 — `ros2 run` 으로 브리지만 손으로 띄우는 경우,
            # 디버깅용 picocom·minicom, 다른 워크스페이스 사본까지 전부 막는다.
            # 338 은 스택 단위, 이것은 장치 단위다.
            new_serial = serial.Serial(
                self._port_name, self._baudrate, timeout=0.2, exclusive=True
            )
            # pyserial이 포트를 열면서 DTR/RTS를 assert된 채로 두면 auto-reset 회로가
            # 보드를 계속 리셋 상태에 붙잡아 앱이 아예 부팅하지 못한다. 명시적으로 풀어준다.
            new_serial.dtr = False
            new_serial.rts = False
        except (serial.SerialException, OSError) as exc:
            # 같은 실패를 매 재시도마다 찍으면 로그가 파괴된다. 보드가 안 붙은
            # 구성에서는 이것이 1Hz 로 영원히 쌓인다(S15P11A301-264 관찰 구간에서
            # 로그가 6142줄이 됐다). 첫 실패만 남기고, 그 뒤로는 실패가 이어지는
            # 동안 조용히 있는다 — 재연결에 성공하면 아래 `connected at` 이
            # 나오므로 상태 전이는 여전히 로그로 읽을 수 있다.
            #
            # **「보드가 없다」와 「남이 쓰고 있다」를 가른다** (S15P11A301-340).
            # 종전에는 둘 다 "not available" 로 뭉뚱그려서, 2026-08-07 에 중복
            # 기동을 진단할 때 이 로그가 아무 단서도 주지 못했다. 포트가 존재하는데
            # 열리지 않으면 점유가 유일하게 가능한 원인이다.
            self._connect_failures += 1
            if self._connect_failures == 1:
                if _port_exists(self._port_name):
                    self._warn(
                        f"{self._port_name} 를 다른 프로세스가 이미 쓰고 있다 ({exc}). "
                        f"스택이 두 벌 떠 있지 않은지 확인하라 — "
                        f"`./scripts/demo_down.sh --dry-run` 이 무엇이 도는지 보여준다. "
                        f"{self._reconnect_delay_s}s 마다 재시도한다 (반복 실패는 더 찍지 않는다)"
                    )
                else:
                    self._warn(
                        f"{self._port_name} not available ({exc}), "
                        f"{self._reconnect_delay_s}s 마다 재시도한다 "
                        f"(반복 실패는 더 찍지 않는다)"
                    )
            return False

        # self._serial에 대입하기 전에 대기한다 - write_frame()이 "연결됨"으로 보고
        # 재부팅 도중에 HELLO를 흘려보내는 레이스를 막는다.
        time.sleep(_BOOT_SETTLE_S)
        try:
            new_serial.reset_input_buffer()  # 대기 중 쌓인 ROM 부팅 배너 잔재를 버린다
        except (serial.SerialException, OSError):
            pass

        with self._serial_lock:
            self._serial = new_serial
        # 재연결 횟수를 함께 남긴다 — 재열거가 몇 번 있었는지가 하드웨어 문제의
        # 단서다. 케이블 접촉이 나쁘면 이 숫자가 조용히 올라간다.
        self._connects += 1
        suffix = "" if self._connects == 1 else f" (재연결 {self._connects - 1}회째)"
        self._connect_failures = 0
        self._info(f"{self._port_name} connected at {self._baudrate}bps{suffix}")
        return True

    def _disconnect(self) -> None:
        with self._serial_lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:  # noqa: BLE001 - 포트 종료 실패는 무시하고 재연결로 넘어간다
                    pass
                self._serial = None

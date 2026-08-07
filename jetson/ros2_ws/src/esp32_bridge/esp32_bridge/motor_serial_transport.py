"""모터 ESP32 전용 pyserial 래퍼 (S15P11A301-321).

`serial_transport.py`(센서가 쓰는, COBS 0x00-구분자 기반)와 포트 연결·재연결·
DTR/RTS·로깅 하드닝은 동일하지만, 프레임 추출 부분만 다르다 - 모터 프레이밍에는
델리미터가 없고 고정 길이 + 동기 워드(motor_protocol_constants.SYNC0/SYNC1)라서,
27바이트 슬라이딩 윈도우가 동기+CRC8을 통과할 때만 큐에 올린다. 실패하면 창을
비우지 않고 다음 바이트가 1바이트 밀게 해 재동기한다(motor_packet_codec.py 상단
설명, ESP32 쪽 comm_task.cpp의 feedByte()와 동일한 알고리즘).

`serial_transport.py`를 공유 모듈로 리팩터링하지 않고 그대로 복제한 이유는
격리다 - 센서 링크가 이미 잘 동작 중이므로, 모터 프레이밍을 바꾸다 실수해도
센서가 쓰는 파일에는 손을 대지 않는다.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import serial

from .motor_packet_codec import crc8
from .motor_protocol_constants import FRAME_BYTES, SYNC0, SYNC1

# ESP32 auto-reset 회로가 DTR/RTS로 GPIO0/EN을 제어한다. DTR/RTS를 막 풀어 보드가
# 리셋에서 빠져나오면 그 순간부터 재부팅이 시작되므로, ESP-IDF 부트로더+FreeRTOS
# 태스크 초기화가 끝날 때까지 짧게 대기해야 그 사이의 ROM 부팅 배너(74880bps라
# 우리 baudrate로는 깨진 바이트로 보인다)를 프로토콜 프레임으로 오인하지 않는다.
_BOOT_SETTLE_S = 1.5


class SerialNotConnectedError(Exception):
    """포트가 열려 있지 않은 상태에서 write_frame을 호출했을 때."""


class MotorSerialTransport:
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

        self._connects = 0
        self._connect_failures = 0

    @property
    def connects(self) -> int:
        """연결 성공 횟수. 1 보다 크면 재연결이 일어났다는 뜻이다."""
        return self._connects

    # 심각도별로 로거 호출을 나누는 이유는 serial_transport.py와 같다
    # (S15P11A301-264 - rclpy 로거는 같은 호출 위치에서 심각도가 바뀌면 예외를
    # 던지고, 그 예외가 리더 스레드를 죽이면 재연결 자체가 사라진다).
    def _info(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.info(message)
        except Exception:  # noqa: BLE001
            pass

    def _warn(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(message)
        except Exception:  # noqa: BLE001
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
                reason = str(exc)

        self._disconnect()
        raise SerialNotConnectedError(
            f"{self._port_name} write failed ({reason}), disconnected for reconnect"
        )

    def read_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """동기+CRC8까지 통과한 원시 프레임(FRAME_BYTES) 하나. timeout 내 없으면 None."""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run(self) -> None:
        """수신 루프 겸 재연결 담당. 하드닝 근거는 serial_transport.py와 동일하다."""
        window = bytearray()
        while not self._stop_event.is_set():
            try:
                self._pump(window)
            except Exception as exc:  # noqa: BLE001
                self._warn(f"{self._port_name} 수신 루프 예외 ({exc}), 계속한다")
                self._disconnect()
                time.sleep(self._reconnect_delay_s)

    def _pump(self, window: bytearray) -> None:
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

        if len(window) < FRAME_BYTES:
            window += byte
        else:
            # 창이 이미 가득 찼다(직전 시도가 동기/CRC 실패) - 1바이트 밀어 재동기.
            del window[0]
            window += byte

        if len(window) < FRAME_BYTES:
            return  # 아직 한 프레임 분량이 안 모였다

        if window[0] == SYNC0 and window[1] == SYNC1:
            body = bytes(window[2:-1])
            if crc8(body) == window[-1]:
                try:
                    self._rx_queue.put_nowait(bytes(window))
                except queue.Full:
                    self._warn(f"{self._port_name} rx queue full, dropping frame")
                window.clear()
                return
        # 동기 불일치거나 CRC 불일치 - 창은 가득 찬 채로 두고 다음 바이트가 위
        # `del window[0]` 분기를 태워 1바이트 밀게 한다(자기 치유 재동기).

    def _connect(self) -> bool:
        try:
            new_serial = serial.Serial(self._port_name, self._baudrate, timeout=0.2)
            new_serial.dtr = False
            new_serial.rts = False
        except (serial.SerialException, OSError) as exc:
            self._connect_failures += 1
            if self._connect_failures == 1:
                self._warn(
                    f"{self._port_name} not available ({exc}), "
                    f"{self._reconnect_delay_s}s 마다 재시도한다 "
                    f"(반복 실패는 더 찍지 않는다)"
                )
            return False

        time.sleep(_BOOT_SETTLE_S)
        try:
            new_serial.reset_input_buffer()
        except (serial.SerialException, OSError):
            pass

        with self._serial_lock:
            self._serial = new_serial
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
                except Exception:  # noqa: BLE001
                    pass
                self._serial = None

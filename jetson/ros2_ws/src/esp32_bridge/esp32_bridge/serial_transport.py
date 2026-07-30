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

import serial

_MAX_ACCUM_BYTES = 256  # MAX_FRAME_BYTES(140)보다 넉넉한 여유


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

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        getattr(self._logger, level)(message)

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
            self._serial.write(frame)

    def read_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """수신된 0x00-구분 COBS 블록 하나를 반환한다. timeout 내 없으면 None."""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _run(self) -> None:
        accum = bytearray()
        while not self._stop_event.is_set():
            with self._serial_lock:
                connected = self._serial is not None and self._serial.is_open
            if not connected:
                if not self._connect():
                    time.sleep(self._reconnect_delay_s)
                    continue

            try:
                byte = self._serial.read(1)
            except (serial.SerialException, OSError) as exc:
                self._log("warn", f"{self._port_name} read failed ({exc}), reconnecting")
                self._disconnect()
                continue

            if not byte:
                continue  # read timeout, 정상

            if byte == b"\x00":
                if accum:
                    try:
                        self._rx_queue.put_nowait(bytes(accum))
                    except queue.Full:
                        self._log("warn", f"{self._port_name} rx queue full, dropping frame")
                    accum.clear()
                continue

            accum += byte
            if len(accum) > _MAX_ACCUM_BYTES:
                self._log("warn", f"{self._port_name} frame too long without delimiter, resetting")
                accum.clear()

    def _connect(self) -> bool:
        try:
            new_serial = serial.Serial(self._port_name, self._baudrate, timeout=0.2)
        except (serial.SerialException, OSError) as exc:
            self._log(
                "warn",
                f"{self._port_name} not available ({exc}), retrying in {self._reconnect_delay_s}s",
            )
            return False
        with self._serial_lock:
            self._serial = new_serial
        self._log("info", f"{self._port_name} connected at {self._baudrate}bps")
        return True

    def _disconnect(self) -> None:
        with self._serial_lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:  # noqa: BLE001 - 포트 종료 실패는 무시하고 재연결로 넘어간다
                    pass
                self._serial = None

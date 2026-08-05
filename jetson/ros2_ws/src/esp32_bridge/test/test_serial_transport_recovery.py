"""SerialTransport 재연결 시험 (S15P11A301-264).

rclpy 없이 돈다 — logger 를 흉내내고 pyserial 을 가짜로 바꿔 끼운다.

## 이 시험이 지키는 것

USB 재열거에서 브리지가 100초 넘게 회복하지 못했다. 원인은 재연결 로직이 아니라
**재연결 경로의 로그 한 줄**이었다 — `_log` 가 모든 심각도를 한 위치에서 보내
rclpy 가 `ValueError: Logger severity cannot be changed between calls.` 를 냈고,
그 예외가 리더 스레드를 죽여 회복이 사라졌다. 프로세스는 살아 있었다.

그래서 여기서 고정하는 것은 셋이다.
  1. 심각도를 섞어도 로깅이 터지지 않는다
  2. 로깅이 터져도 리더 스레드가 죽지 않는다
  3. 쓰기 실패가 포트를 닫아 재연결을 시작시킨다
"""

import threading
import time

import pytest
import serial
from esp32_bridge.serial_transport import (SerialNotConnectedError,
                                           SerialTransport)


class SeverityStrictLogger:
    """rclpy 로거의 문제 동작을 재현한다.

    같은 호출 위치에서 심각도가 바뀌면 거부한다. 위치를 알 수 없으므로
    '직전 심각도와 다르면 거부' 로 흉내낸다 — 원본보다 엄격하지만, 통과하면
    원본에서도 통과한다.
    """

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self._last: str | None = None

    def _check(self, level: str) -> None:
        if self._last is not None and self._last != level:
            raise ValueError('Logger severity cannot be changed between calls.')

    def info(self, message: str, **_kw) -> None:
        self._check('info')
        self.infos.append(message)

    def warning(self, message: str, **_kw) -> None:
        self._check('warning')
        self.warnings.append(message)


class TolerantLogger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, message: str, **_kw) -> None:
        self.infos.append(message)

    def warning(self, message: str, **_kw) -> None:
        self.warnings.append(message)


class FakeSerial:
    """열림/쓰기 실패를 흉내내는 최소 시리얼."""

    def __init__(self, *, write_error: Exception | None = None) -> None:
        self.is_open = True
        self.dtr = True
        self.rts = True
        self.written: list[bytes] = []
        self.closed = False
        self._write_error = write_error

    def write(self, data: bytes) -> None:
        if self._write_error is not None:
            raise self._write_error
        self.written.append(data)

    def read(self, _n: int) -> bytes:
        time.sleep(0.01)
        return b''

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def _transport(logger, **kw) -> SerialTransport:
    return SerialTransport('/dev/fake', 921600, logger=logger,
                           reconnect_delay_s=0.01, **kw)


# ── 1. 로깅이 심각도를 섞어도 터지지 않는다 ──────────────────────────────


def test_심각도를_섞어도_로깅이_터지지_않는다():
    """`_log(level, ...)` 한 줄로 보내던 것이 여기서 터졌다."""
    logger = SeverityStrictLogger()
    t = _transport(logger)

    t._info('첫 정보')
    t._warn('그 다음 경고')      # 종전 구현이라면 ValueError
    t._info('다시 정보')

    assert logger.infos == ['첫 정보', '다시 정보']
    assert logger.warnings == ['그 다음 경고']


def test_로거가_터져도_호출부로_예외가_새지_않는다():
    class ExplodingLogger:
        def info(self, *_a, **_kw):
            raise RuntimeError('로거 고장')

        def warning(self, *_a, **_kw):
            raise RuntimeError('로거 고장')

    t = _transport(ExplodingLogger())

    t._info('무시된다')   # 예외가 나오면 실패
    t._warn('무시된다')


def test_로거가_없어도_동작한다():
    t = SerialTransport('/dev/fake', logger=None)
    t._info('버려진다')
    t._warn('버려진다')


# ── 2. 리더 스레드가 어떤 예외로도 죽지 않는다 ───────────────────────────


def test_수신_루프는_예외를_먹고_계속_돈다(monkeypatch):
    """이 스레드가 재연결 담당이다. 죽으면 회복이 사라진다."""
    logger = TolerantLogger()
    t = _transport(logger)
    calls = {'n': 0}

    def exploding_pump(_accum):
        calls['n'] += 1
        raise RuntimeError('설명할 수 없는 고장')

    monkeypatch.setattr(t, '_pump', exploding_pump)
    t.open()
    time.sleep(0.15)
    alive = t._reader_thread.is_alive()
    t._stop_event.set()
    t.close()

    assert alive, '리더 스레드가 죽었다 — 재연결이 사라진다'
    assert calls['n'] > 1, '한 번 터진 뒤 다시 돌지 않았다'
    assert any('수신 루프 예외' in m for m in logger.warnings)


# ── 3. 쓰기 실패가 재연결을 시작시킨다 ───────────────────────────────────


def test_쓰기가_EIO_를_내면_포트를_닫는다():
    """닫지 않으면 죽은 fd 로 영원히 재시도한다 — 실측된 증상이다."""
    logger = TolerantLogger()
    t = _transport(logger)
    fake = FakeSerial(write_error=serial.SerialException(
        'write failed: [Errno 5] Input/output error'))
    t._serial = fake

    with pytest.raises(SerialNotConnectedError) as caught:
        t.write_frame(b'\x01\x02')

    assert fake.closed, '포트를 닫지 않았다 — 재연결이 시작되지 않는다'
    assert t._serial is None
    assert 'Errno 5' in str(caught.value)


def test_OSError_도_같이_처리한다():
    t = _transport(TolerantLogger())
    fake = FakeSerial(write_error=OSError(5, 'Input/output error'))
    t._serial = fake

    with pytest.raises(SerialNotConnectedError):
        t.write_frame(b'\x01')

    assert fake.closed


def test_정상_쓰기는_포트를_닫지_않는다():
    t = _transport(TolerantLogger())
    fake = FakeSerial()
    t._serial = fake

    t.write_frame(b'\x01\x02')

    assert fake.written == [b'\x01\x02']
    assert not fake.closed
    assert t._serial is fake


def test_포트가_없으면_예외를_낸다():
    t = _transport(TolerantLogger())

    with pytest.raises(SerialNotConnectedError):
        t.write_frame(b'\x01')


# ── 4. 재연결이 실제로 일어난다 ──────────────────────────────────────────


def test_끊긴_뒤_새_장치로_다시_연결한다(monkeypatch):
    """재열거로 장치 번호가 바뀌어도 심링크를 다시 열면 성공한다."""
    logger = TolerantLogger()
    t = _transport(logger)
    monkeypatch.setattr('esp32_bridge.serial_transport._BOOT_SETTLE_S', 0.0)

    opened = []

    def fake_ctor(port, baudrate, timeout=None):
        opened.append(port)
        return FakeSerial()

    monkeypatch.setattr(serial, 'Serial', fake_ctor)

    assert t._connect() is True
    assert t.connects == 1

    t._disconnect()
    assert t._serial is None

    assert t._connect() is True
    assert t.connects == 2
    assert opened == ['/dev/fake', '/dev/fake']
    # 재연결 횟수가 로그에 남아야 한다 — 케이블 접촉 문제의 단서다.
    assert any('재연결 1회째' in m for m in logger.infos)


def test_연결_실패는_한_번만_찍는다(monkeypatch):
    """보드가 안 붙은 구성에서 이것이 1Hz 로 영원히 쌓였다."""
    logger = TolerantLogger()
    t = _transport(logger)

    def always_fail(*_a, **_kw):
        raise serial.SerialException('no such device')

    monkeypatch.setattr(serial, 'Serial', always_fail)

    for _ in range(20):
        assert t._connect() is False

    assert len(logger.warnings) == 1, f'경고가 {len(logger.warnings)}건 — 폭주한다'


def test_재연결_성공이_실패_카운터를_되돌린다(monkeypatch):
    """다음 끊김에서 첫 실패가 다시 보고돼야 한다."""
    logger = TolerantLogger()
    t = _transport(logger)
    monkeypatch.setattr('esp32_bridge.serial_transport._BOOT_SETTLE_S', 0.0)

    def always_fail(*_a, **_kw):
        raise serial.SerialException('no such device')

    monkeypatch.setattr(serial, 'Serial', always_fail)
    t._connect()
    t._connect()
    assert len(logger.warnings) == 1

    monkeypatch.setattr(serial, 'Serial',
                        lambda port, baudrate, timeout=None: FakeSerial())
    assert t._connect() is True

    monkeypatch.setattr(serial, 'Serial', always_fail)
    t._disconnect()
    t._connect()

    assert len(logger.warnings) == 2, '두 번째 끊김의 첫 실패가 보고되지 않았다'

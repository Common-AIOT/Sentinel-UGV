"""센서 ESP32 monotonic 시계(µs) -> ROS 시각 오프셋 추정 (S15P11A301-244).

`IMU_STATE.sample_time_us`는 보드 부팅 후 경과 µs(`esp_timer_get_time()`)다. §34-5는
**수신 시각을 측정 시각으로 대신하지 말고** 오프셋을 구해 변환하라고 정하고 있다.
직렬 도착 지터가 그대로 IMU 타임스탬프 잡음이 되면 EKF가 각속도를 잘못 적분한다.

## 추정 방법

    ros_recv = board_sample + offset + delay,    delay >= 0

`delay`는 I2C 판독 완료 시각부터 UART 전송·pyserial 큐·GIL 스케줄까지의 합이라 항상
양수다. 따라서 관측한 `ros_recv - board_sample` 중 **최솟값이 참 offset에 가장
가깝다**(min filter). 평균을 쓰면 지터의 평균 지연만큼 통째로 늦게 찍힌다.

최솟값을 영구히 들고 있으면 안 된다. ESP32 크리스털과 Jetson 시계가 서로 흐르므로
(수십~100ppm) 오래된 최솟값은 시간이 갈수록 틀려진다. 그래서 `resync_period_s`
주기의 창으로 잘라 창마다 최솟값을 다시 채택한다 - 오차가 "창 길이 × 드리프트"로
묶인다(10초·100ppm이면 1ms).

## 재부팅

보드가 재부팅하면 `esp_timer`가 0에서 다시 시작하므로 `sample_time_us`가 **되돌아간다.**
monotonic 시계에서 이것은 재부팅에서만 일어나므로 판정 근거로 쓴다. §34-5대로 기존
offset을 버리고 다시 동기화한다.

"candidate offset이 크게 뛰면 재부팅"으로 판정하지 않는다. Jetson 쪽 수신 스레드가
CPU 부하로 1초 이상 굶어도 같은 크기로 뛰는데, 그때 offset을 다시 잡으면 굶은 시간이
전송 지연으로 오인되어 스탬프가 그만큼 늦어진다. 그 경우 낡은 offset을 그대로 두는
편이 정확하다(참값은 변하지 않았다).

`settled`가 false인 동안(초기 몇 샘플)에는 offset이 전송 지연을 그대로 품고 있다.
노드는 이 구간의 샘플을 발행하되 공분산을 크게 실어 EKF가 융합하지 않게 한다
("이 변환이 끝나기 전의 IMU 샘플은 EKF에 넣지 않는다", §34-5).

`rclpy`를 import하지 않는 순수 로직이라 ROS 없이 pytest로 검증한다.
"""

from __future__ import annotations

_NS_PER_US = 1_000

# 창 하나 안의 최솟값을 채택하기까지의 길이. 100Hz면 창마다 ~1000 샘플이 모인다.
_DEFAULT_RESYNC_PERIOD_S = 10.0

# 이만큼의 샘플을 보기 전까지는 offset이 전송 지연을 품고 있다고 본다.
# 100Hz에서 20샘플 = 약 200ms.
_DEFAULT_SETTLE_SAMPLES = 20


class BoardClockOffset:
    """보드 monotonic µs -> ROS ns 변환 오프셋(min filter + 주기적 재동기)."""

    def __init__(
        self,
        *,
        resync_period_s: float = _DEFAULT_RESYNC_PERIOD_S,
        settle_samples: int = _DEFAULT_SETTLE_SAMPLES,
    ) -> None:
        self._resync_period_ns = int(resync_period_s * 1e9)
        self._settle_samples = settle_samples

        self._offset_ns: int | None = None
        self._window_min_ns: int | None = None
        self._window_start_ns: int | None = None
        self._last_board_ns: int | None = None
        self._sample_count = 0
        self._resync_count = 0

    # ---- 상태 조회 ----

    @property
    def offset_ns(self) -> int | None:
        return self._offset_ns

    @property
    def settled(self) -> bool:
        """offset이 전송 지연을 걷어낼 만큼 샘플을 모았는지."""
        return self._offset_ns is not None and self._sample_count >= self._settle_samples

    @property
    def resync_count(self) -> int:
        """보드 재부팅으로 offset을 버린 횟수(창 단위 재채택은 세지 않는다)."""
        return self._resync_count

    def reset(self) -> None:
        """보드 재부팅 등으로 기존 offset을 폐기한다(§34-5)."""
        if self._offset_ns is not None:
            self._resync_count += 1
        self._offset_ns = None
        self._window_min_ns = None
        self._window_start_ns = None
        self._last_board_ns = None
        self._sample_count = 0

    # ---- 변환 ----

    def update(self, sample_time_us: int, receive_time_ns: int) -> int:
        """샘플 하나를 반영하고 ROS 시각(ns)을 돌려준다."""
        board_ns = sample_time_us * _NS_PER_US

        if self._last_board_ns is not None and board_ns < self._last_board_ns:
            # monotonic 시계가 되돌아갔다 = 보드 재부팅. 기존 offset을 폐기한다.
            self.reset()
        self._last_board_ns = board_ns

        candidate_ns = receive_time_ns - board_ns

        if self._offset_ns is None:
            self._offset_ns = candidate_ns
            self._window_min_ns = candidate_ns
            self._window_start_ns = receive_time_ns
        else:
            if candidate_ns < self._offset_ns:
                # 더 짧은 지연을 봤다 - 참값에 더 가깝다.
                self._offset_ns = candidate_ns
            if candidate_ns < self._window_min_ns:
                self._window_min_ns = candidate_ns
            if receive_time_ns - self._window_start_ns >= self._resync_period_ns:
                # 창을 닫고 이 창의 최솟값을 채택한다(드리프트 추종).
                self._offset_ns = self._window_min_ns
                self._window_min_ns = candidate_ns
                self._window_start_ns = receive_time_ns

        self._sample_count += 1

        stamp_ns = self._offset_ns + board_ns
        # 측정 시각이 수신 시각보다 미래일 수는 없다. 드리프트 보정 직후의
        # 경계에서만 생길 수 있는데, 미래 스탬프는 tf2/EKF가 즉시 버린다.
        return min(stamp_ns, receive_time_ns)

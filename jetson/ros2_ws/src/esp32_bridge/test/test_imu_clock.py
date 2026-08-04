"""imu_clock.BoardClockOffset 시험 (S15P11A301-244).

rclpy를 import하지 않으므로 ROS 없이 돈다. 시간은 전부 인자로 넣으므로 실제 시계를
쓰지 않는다 - 테스트가 느려지거나 CI 부하에 따라 흔들리지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.imu_clock import BoardClockOffset  # noqa: E402

_MS = 1_000_000  # ns
_S = 1_000_000_000  # ns

# 보드 부팅과 ROS 시각의 기준 차이. 테스트에서 복원해야 하는 참 offset이다.
_TRUE_OFFSET_NS = 1_700_000_000 * _S


def _receive_ns(sample_time_us: int, delay_ns: int) -> int:
    """측정 시각 sample_time_us의 샘플이 delay_ns만큼 늦게 도착했을 때의 수신 시각."""
    return _TRUE_OFFSET_NS + sample_time_us * 1000 + delay_ns


def test_first_sample_stamps_immediately_but_is_not_settled():
    clock = BoardClockOffset()
    stamp = clock.update(1_000, _receive_ns(1_000, 3 * _MS))

    # 첫 샘플은 전송 지연을 그대로 품는다 - 발행은 하되 EKF에 넣지 않는다(§34-5).
    assert clock.settled is False
    assert stamp == _receive_ns(1_000, 3 * _MS)


def test_min_filter_converges_to_true_offset_despite_jitter():
    clock = BoardClockOffset(settle_samples=5)
    # 지연이 2~10ms로 흔들리고 그중 한 번 2ms가 최솟값이다.
    for index, delay_ms in enumerate([9, 4, 10, 2, 7, 8, 5]):
        sample_us = 10_000 * (index + 1)
        clock.update(sample_us, _receive_ns(sample_us, delay_ms * _MS))

    assert clock.settled is True
    # min filter는 최소 지연만큼만 늦다. 평균을 쓰면 6ms쯤 늦게 찍힌다.
    assert clock.offset_ns == _TRUE_OFFSET_NS + 2 * _MS


def test_stamp_never_exceeds_receive_time():
    clock = BoardClockOffset()
    for index in range(10):
        sample_us = 10_000 * (index + 1)
        receive_ns = _receive_ns(sample_us, 5 * _MS)
        assert clock.update(sample_us, receive_ns) <= receive_ns


def test_stamps_track_sample_spacing_not_arrival_spacing():
    """도착 지터가 스탬프 간격으로 새어 들어가지 않아야 한다."""
    clock = BoardClockOffset(settle_samples=1)
    clock.update(10_000, _receive_ns(10_000, 2 * _MS))
    # 다음 샘플은 정확히 10ms 뒤에 측정됐지만 도착은 20ms 뒤로 밀렸다.
    first = clock.update(20_000, _receive_ns(20_000, 2 * _MS))
    second = clock.update(30_000, _receive_ns(30_000, 12 * _MS))

    assert second - first == 10 * _MS


def test_offset_holds_window_minimum_against_jitter():
    clock = BoardClockOffset(resync_period_s=1.0, settle_samples=1)
    clock.update(10_000, _receive_ns(10_000, 2 * _MS))
    baseline = clock.offset_ns

    # 같은 창 안에서 지연이 커져도 offset은 최솟값을 유지한다.
    clock.update(500_000, _receive_ns(500_000, 9 * _MS))
    assert clock.offset_ns == baseline


def test_resync_window_follows_clock_drift():
    """보드 시계가 ROS보다 느리면 candidate offset이 계속 커진다.

    min filter만 있으면 최초 최솟값에 영구히 묶여 오차가 무한히 자란다. 창을 닫을
    때마다 그 창의 최솟값을 다시 채택해 한 창(여기서는 1초)만큼 늦게 따라간다.
    """
    clock = BoardClockOffset(resync_period_s=1.0, settle_samples=1)

    # 창 1(0~1s): 최소 지연 2ms.
    clock.update(10_000, _receive_ns(10_000, 2 * _MS))
    clock.update(500_000, _receive_ns(500_000, 6 * _MS))
    assert clock.offset_ns == _TRUE_OFFSET_NS + 2 * _MS

    # 창 1을 닫는 샘플. 채택값은 창 1의 최솟값 그대로이고, 창 2는 이 샘플부터다.
    clock.update(1_100_000, _receive_ns(1_100_000, 4 * _MS))
    assert clock.offset_ns == _TRUE_OFFSET_NS + 2 * _MS

    # 창 2(1.1~2.2s): 드리프트로 최소 지연이 4ms까지 밀렸다.
    clock.update(1_500_000, _receive_ns(1_500_000, 7 * _MS))
    clock.update(2_300_000, _receive_ns(2_300_000, 5 * _MS))
    assert clock.offset_ns == _TRUE_OFFSET_NS + 4 * _MS

    # 창 재채택은 재부팅이 아니므로 resync_count는 오르지 않는다.
    assert clock.resync_count == 0


def test_board_reboot_discards_stale_offset():
    """재부팅 판정은 sample_time_us 역행으로 한다(monotonic 시계에서 그것뿐이다)."""
    clock = BoardClockOffset(settle_samples=2)
    for index in range(5):
        sample_us = 10_000 * (index + 1) + 60 * 1_000_000  # 부팅 후 60초 지점
        clock.update(sample_us, _receive_ns(sample_us, 2 * _MS))
    assert clock.settled is True

    # 보드가 재부팅하면 sample_time_us가 0 근처로 돌아간다. 낡은 offset을 그대로
    # 쓰면 스탬프가 60초 미래로 찍혀 tf2/EKF가 전부 버린다.
    reboot_sample_us = 5_000
    reboot_receive_ns = _receive_ns(60 * 1_000_000, 70 * _MS)
    stamp = clock.update(reboot_sample_us, reboot_receive_ns)

    assert clock.resync_count == 1
    assert clock.settled is False  # 다시 모으는 동안은 EKF에 넣지 않는다
    assert stamp == reboot_receive_ns


def test_receive_side_stall_does_not_look_like_a_reboot():
    """Jetson 수신 스레드가 굶어 도착이 몰려도 offset을 다시 잡지 않는다.

    "candidate offset이 크게 뛰면 재부팅"으로 판정하면 이 경우가 오진에 걸려, 굶은
    시간이 전송 지연으로 오인되어 스탬프가 그만큼 늦어진다. 보드 시계는 계속 흘렀고
    참 offset은 변하지 않았으므로 낡은 offset이 정답이다.
    """
    clock = BoardClockOffset(settle_samples=2)
    for index in range(5):
        sample_us = 10_000 * (index + 1)
        clock.update(sample_us, _receive_ns(sample_us, 2 * _MS))
    settled_offset = clock.offset_ns

    # 수신이 3초 밀려 도착한 샘플(보드 시각은 정상적으로 전진했다).
    stalled_sample_us = 3_100_000
    clock.update(stalled_sample_us, _receive_ns(stalled_sample_us, 3 * _S))

    assert clock.resync_count == 0
    assert clock.offset_ns == settled_offset
    assert clock.settled is True


def test_reset_clears_offset_and_counts_resync():
    clock = BoardClockOffset(settle_samples=1)
    clock.update(10_000, _receive_ns(10_000, 2 * _MS))
    clock.reset()

    assert clock.offset_ns is None
    assert clock.settled is False
    assert clock.resync_count == 1

    # 오프셋이 없는 상태에서 다시 시작해도 첫 샘플로 곧바로 복구한다.
    stamp = clock.update(20_000, _receive_ns(20_000, 3 * _MS))
    assert stamp == _receive_ns(20_000, 3 * _MS)


def test_reset_without_offset_does_not_count_resync():
    clock = BoardClockOffset()
    clock.reset()
    assert clock.resync_count == 0

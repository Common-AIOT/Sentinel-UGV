"""모터 보드로 나가는 마지막 Jetson 안전 한계의 회귀 시험."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esp32_bridge.drive_command_filter import (  # noqa: E402
    DriveCommandLimits,
    filter_drive_targets,
)


LIMITS = DriveCommandLimits(max_drive_mmps=300, max_steering_mdeg=22_000)


def test_한계_안쪽_명령은_그대로_통과한다():
    result = filter_drive_targets(
        left_mmps=150,
        right_mmps=-200,
        steering_mdeg=12_000,
        limits=LIMITS,
    )

    assert (result.left_mmps, result.right_mmps, result.steering_mdeg) == (
        150,
        -200,
        12_000,
    )
    assert result.was_filtered is False
    assert result.filtered_fields == ()


@pytest.mark.parametrize("sign", [-1, 1])
def test_정확한_양쪽_경계값은_필터링하지_않는다(sign):
    result = filter_drive_targets(
        left_mmps=sign * 300,
        right_mmps=sign * 300,
        steering_mdeg=sign * 22_000,
        limits=LIMITS,
    )

    assert result.was_filtered is False


def test_양수와_음수_초과값을_각각_대칭_포화한다():
    result = filter_drive_targets(
        left_mmps=999,
        right_mmps=-999,
        steering_mdeg=-40_000,
        limits=LIMITS,
    )

    assert (result.left_mmps, result.right_mmps, result.steering_mdeg) == (
        300,
        -300,
        -22_000,
    )
    assert result.filtered_fields == (
        "target_drive_left_mmps",
        "target_drive_right_mmps",
        "target_steering_mdeg",
    )


def test_초과한_필드만_독립적으로_필터링한다():
    result = filter_drive_targets(
        left_mmps=301,
        right_mmps=299,
        steering_mdeg=22_001,
        limits=LIMITS,
    )

    assert (result.left_mmps, result.right_mmps, result.steering_mdeg) == (
        300,
        299,
        22_000,
    )
    assert result.filtered_fields == (
        "target_drive_left_mmps",
        "target_steering_mdeg",
    )


@pytest.mark.parametrize(
    ("max_drive_mmps", "max_steering_mdeg"),
    [(0, 22_000), (-1, 22_000), (300, 0), (300, 32_768)],
)
def test_잘못된_한계_설정은_기동_전에_거부한다(
    max_drive_mmps, max_steering_mdeg
):
    with pytest.raises(ValueError):
        DriveCommandLimits(
            max_drive_mmps=max_drive_mmps,
            max_steering_mdeg=max_steering_mdeg,
        )

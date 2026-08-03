"""ESP32 실측값 변환 시험 (S15P11A301-213).

`environment_payload`와 `motion_payload`는 rclpy를 import하지 않으므로 CI에서
돈다. 이 두 함수에 시험을 붙이는 이유는 여기서 틀리면 **검증을 통과하는
잘못된 값**이 나오기 때문이다.

* 습도를 100으로 곱하지 않으면 0.65가 그대로 간다. 스키마 범위(0~100) 안이라
  계약 검증도 통과하고 화면에도 뜬다. 값만 틀린다.
* NaN은 `json.dumps`가 `NaN` 리터럴로 직렬화하고 그것은 유효한 JSON이 아니다.
  필드 하나가 비는 것이 아니라 telemetry 봉투 전체가 버려진다.

두 경우 모두 스택을 띄워서는 알아채기 어렵고, 관제 화면은 정상으로 보인다.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_bridge.message_mapper import (  # noqa: E402
    environment_payload,
    finite_or_none,
    motion_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_PATH = REPO_ROOT / "common" / "schemas" / "telemetry.schema.json"


# ----------------------------------------------------------------------
# environment
# ----------------------------------------------------------------------


def test_humidity_ratio_becomes_percent():
    """sensor_msgs/RelativeHumidity는 0~1이고 스키마는 0~100이다."""
    payload = environment_payload(28.2, 0.651)
    assert payload == {"temperatureC": 28.2, "humidityPercent": 65.1}


def test_humidity_is_not_left_as_ratio():
    """변환을 빼먹으면 이 값이 0.651로 남는다. 스키마는 그것도 받아들인다."""
    payload = environment_payload(28.2, 0.651)
    assert payload is not None
    assert payload["humidityPercent"] > 1.0


@pytest.mark.parametrize("ratio,expected", [(1.004, 100.0), (-0.001, 0.0)])
def test_humidity_out_of_range_is_clamped(ratio, expected):
    """센서 잡음이 범위를 살짝 넘어도 온습도가 통째로 사라지지 않게 한다.

    스키마가 0~100을 강제하므로 그대로 보내면 봉투 전체가 거부된다.
    """
    payload = environment_payload(20.0, ratio)
    assert payload is not None
    assert payload["humidityPercent"] == expected


@pytest.mark.parametrize(
    "temperature,humidity",
    [
        (None, 0.5),
        (20.0, None),
        (float("nan"), 0.5),
        (20.0, float("nan")),
        (float("inf"), 0.5),
        (20.0, float("-inf")),
    ],
)
def test_environment_is_none_when_a_value_is_missing(temperature, humidity):
    """스키마가 두 필드를 모두 required로 두었으므로 반쪽 객체를 만들지 않는다."""
    assert environment_payload(temperature, humidity) is None


def test_environment_is_json_serialisable_strictly():
    """NaN이 새면 여기서 걸린다. allow_nan=False가 표준 JSON이다."""
    payload = environment_payload(28.2, 0.651)
    assert json.dumps(payload, allow_nan=False)


# ----------------------------------------------------------------------
# motion
# ----------------------------------------------------------------------


def test_motion_maps_twist_axes():
    payload = motion_payload(0.32, -0.15)
    assert payload == {"linearVelocityMps": 0.32, "angularVelocityRadps": -0.15}


def test_motion_keeps_zero_as_zero():
    """정지는 값이 0인 상태이고 "모름"이 아니다. None으로 바꾸면 안 된다."""
    assert motion_payload(0.0, 0.0) == {
        "linearVelocityMps": 0.0,
        "angularVelocityRadps": 0.0,
    }


@pytest.mark.parametrize(
    "linear,angular",
    [(None, 0.0), (0.0, None), (float("nan"), 0.0), (0.0, float("inf"))],
)
def test_motion_is_none_when_a_value_is_missing(linear, angular):
    assert motion_payload(linear, angular) is None


# ----------------------------------------------------------------------
# finite_or_none
# ----------------------------------------------------------------------


def test_bool_is_not_treated_as_number():
    """파이썬에서 bool은 int의 하위형이다. True가 1.0으로 새면 안 된다."""
    assert finite_or_none(True) is None
    assert finite_or_none(False) is None


def test_int_is_accepted_as_float():
    assert finite_or_none(20) == 20.0


def test_values_are_rounded_to_sensor_resolution():
    """부동소수 잔여값을 그대로 보내지 않는다.

    0.651 * 100 은 65.10000000000001이다. 2Hz로 적재되는 값이라 자릿수가 그대로
    저장 용량이 되고, DHT11의 분해능은 0.1이다.
    """
    payload = environment_payload(28.24999, 0.651)
    assert payload == {"temperatureC": 28.2, "humidityPercent": 65.1}
    assert motion_payload(0.3216789, -0.1543210) == {
        "linearVelocityMps": 0.322,
        "angularVelocityRadps": -0.154,
    }


@pytest.mark.parametrize("value", ["20.0", None, [], {}, object()])
def test_non_numeric_is_none(value):
    assert finite_or_none(value) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_is_none(value):
    assert finite_or_none(value) is None
    assert not math.isfinite(value)


# ----------------------------------------------------------------------
# 스키마와 실제로 맞는지
# ----------------------------------------------------------------------


def test_payloads_match_schema_shape():
    """필드 이름과 타입을 스키마에서 직접 읽어 비교한다.

    이름을 손으로 적어 두면 스키마가 바뀔 때 이 시험이 같이 틀리지 않는다.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    environment = schema["properties"]["environment"]
    payload = environment_payload(28.2, 0.651)
    assert payload is not None
    assert set(payload) == set(environment["required"])
    assert environment["properties"]["humidityPercent"]["minimum"] == 0
    assert environment["properties"]["humidityPercent"]["maximum"] == 100
    assert 0 <= payload["humidityPercent"] <= 100

    motion = schema["properties"]["motion"]
    assert set(motion_payload(0.1, 0.2) or {}) == set(motion["required"])


def test_battery_stays_nullable_in_schema():
    """S15P11A301-174에 전압 계측이 없어 battery는 null로 남는다.

    스키마가 null을 허용하지 않게 바뀌면 그 사실이 먼저 깨지므로 여기서 잡는다.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "null" in schema["properties"]["battery"]["type"]

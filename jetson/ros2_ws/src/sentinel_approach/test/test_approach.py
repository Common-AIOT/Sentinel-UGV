"""접근 판정 시험 (S15P11A301-247).

고정하는 것 셋이다.

1. **제자리 회전을 절대 내지 않는다.** 전륜 조향 차량이 그것을 못 하고,
   `vehicle_kinematics` 가 거부하며, Nav2 진행 검사가 그것을 정체로 읽는다.
2. **거리를 모르면 멈춘다.** 모르는 것과 먼 것을 섞지 않는다.
3. **곡률이 `1/R_min` 을 넘지 않는다.** 넘는 명령은 차체가 따라갈 수 없다.
"""

import math

import pytest

from sentinel_approach.approach import (
    ApproachCommand,
    ApproachLimits,
    bearing_from_box,
    plan_approach,
    range_at_bearing,
)

HFOV = math.radians(52.0)  # BRIO 100, exploration_node 와 같은 잠정값
LIMITS = ApproachLimits()


class TestBearing:
    def test_중앙은_0이다(self):
        assert bearing_from_box(640, 1280, HFOV) == pytest.approx(0.0)

    def test_오른쪽은_음수_왼쪽은_양수다(self):
        # ROS 오른손 좌표계: +z 가 위이므로 왼쪽이 양의 yaw 다.
        assert bearing_from_box(1280, 1280, HFOV) < 0
        assert bearing_from_box(0, 1280, HFOV) > 0

    def test_가장자리는_화각_절반이다(self):
        assert bearing_from_box(0, 1280, HFOV) == pytest.approx(HFOV / 2)

    def test_폭이_0이면_거부한다(self):
        with pytest.raises(ValueError):
            bearing_from_box(10, 0, HFOV)


class TestRangeAtBearing:
    def _scan(self, values):
        # -90°~+90°, 1° 간격
        return dict(
            ranges=values,
            angle_min=math.radians(-90),
            angle_increment=math.radians(1),
        )

    def test_그_방위의_중앙값을_쓴다(self):
        values = [10.0] * 181
        for i in range(88, 93):  # 정면 부근 5개
            values[i] = 2.0
        got = range_at_bearing(**self._scan(values), bearing_rad=0.0, window=2)
        assert got == pytest.approx(2.0)

    def test_옆으로_빠진_빔_하나에_끌려가지_않는다(self):
        # 사람 옆을 스쳐 뒤 벽까지 간 빔이 섞인다. 평균이면 그 하나가 값을 끌어올린다.
        values = [10.0] * 181
        values[88] = 2.0
        values[89] = 2.0
        values[90] = 2.0
        values[91] = 2.0
        values[92] = 30.0
        got = range_at_bearing(**self._scan(values), bearing_rad=0.0, window=2)
        assert got == pytest.approx(2.0)

    def test_유효_빔이_없으면_None(self):
        values = [float('nan')] * 181
        assert range_at_bearing(**self._scan(values), bearing_rad=0.0) is None

    def test_범위_밖_값은_버린다(self):
        values = [0.0] * 181
        got = range_at_bearing(**self._scan(values), bearing_rad=0.0, range_min=0.05)
        assert got is None


class TestPlanApproach:
    def test_거리를_모르면_멈춘다(self):
        cmd = plan_approach(bearing_rad=0.3, distance_m=None, limits=LIMITS)
        assert cmd.linear_mps == 0.0
        assert cmd.angular_radps == 0.0
        assert cmd.arrived is False
        assert cmd.reason == 'no_range'

    def test_안전거리_안이면_도착이다(self):
        cmd = plan_approach(bearing_rad=0.2, distance_m=0.5, limits=LIMITS)
        assert cmd.arrived is True
        assert cmd.linear_mps == 0.0

    def test_멀면_전진하며_조향한다(self):
        cmd = plan_approach(bearing_rad=0.2, distance_m=3.0, limits=LIMITS)
        assert cmd.linear_mps == pytest.approx(LIMITS.max_speed_mps)
        assert cmd.angular_radps > 0  # 왼쪽으로 감는다
        assert cmd.arrived is False

    def test_제자리_회전을_내지_않는다(self):
        # 이 모듈의 핵심 불변식. 방위 오차가 아무리 커도 v=0·ω≠0 은 없다.
        for bearing in (-1.5, -0.5, 0.0, 0.5, 1.5):
            for distance in (None, 0.1, 0.6, 5.0):
                cmd = plan_approach(
                    bearing_rad=bearing, distance_m=distance, limits=LIMITS
                )
                assert not (cmd.linear_mps == 0.0 and cmd.angular_radps != 0.0)

    def test_곡률이_R_min_을_넘지_않는다(self):
        cmd = plan_approach(bearing_rad=1.2, distance_m=3.0, limits=LIMITS)
        curvature = abs(cmd.angular_radps / cmd.linear_mps)
        assert curvature <= LIMITS.max_curvature + 1e-9

    def test_상태의_속도_상한이_더_낮으면_그것을_쓴다(self):
        cmd = plan_approach(
            bearing_rad=0.1, distance_m=3.0, limits=LIMITS, speed_limit_mps=0.05
        )
        assert cmd.linear_mps == pytest.approx(0.05)

    def test_상한이_0이면_멈춘다(self):
        cmd = plan_approach(
            bearing_rad=0.4, distance_m=3.0, limits=LIMITS, speed_limit_mps=0.0
        )
        assert cmd.linear_mps == 0.0
        assert cmd.angular_radps == 0.0

    def test_정지거리는_충돌_정지구역보다_크다(self):
        # 0.40m 는 collision_monitor 의 전방 정지 구역(24.1)이다. 그보다 작게 잡으면
        # 안전 체인이 먼저 0 을 만들어 도착 선언이 영원히 안 나온다.
        assert LIMITS.stop_distance_m > 0.40


class TestInvariantIsEnforced:
    def test_자료형이_제자리_회전을_거부한다(self):
        with pytest.raises(ValueError):
            ApproachCommand(0.0, 0.3, False, 'spin')

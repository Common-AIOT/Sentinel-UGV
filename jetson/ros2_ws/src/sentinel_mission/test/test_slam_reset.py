"""slam 재시작(지도 초기화) 시험 (S15P11A301-362).

rclpy 없이 순수 함수만 시험한다 — 프로세스 신호는 runner 주입으로 목킹.
"""

from __future__ import annotations

import subprocess

from sentinel_mission.slam_reset import SLAM_PROCESS_PATTERN, reset_slam_process


class _Result:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def test_slam_이_있으면_TERM_을_보내고_True():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _Result(0)

    assert reset_slam_process(runner) is True
    assert calls == [['pkill', '-TERM', '-f', SLAM_PROCESS_PATTERN]]


def test_slam_이_없으면_False():
    # pkill 은 일치 프로세스가 없으면 1 을 낸다 — enable_slam 없는 구성에서 정상.
    assert reset_slam_process(lambda cmd, **kw: _Result(1)) is False


def test_실행_실패도_False_로_삼킨다():
    # 신호를 못 보냈다고 임무 시작을 막지 않는다 — 호출부가 경고만 남긴다.
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    assert reset_slam_process(runner) is False


def test_패턴이_자기_자신과_안_겹친다():
    # pkill -f 는 전체 cmdline 을 본다. mission_manager 실행 파일 경로에 이
    # 패턴이 들어가면 자기 자신을 죽인다 — 그 사고를 이름 규약으로 막는다.
    assert 'mission' not in SLAM_PROCESS_PATTERN
    assert SLAM_PROCESS_PATTERN == 'async_slam_toolbox_node'

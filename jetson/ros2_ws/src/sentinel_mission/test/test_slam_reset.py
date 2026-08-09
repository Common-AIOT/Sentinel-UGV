"""slam 재시작(지도 초기화) 시험 (S15P11A301-362).

rclpy 없이 순수 함수만 시험한다 — 프로세스 신호와 탐색은 주입으로 목킹.
"""

from __future__ import annotations

import os
import signal

from sentinel_mission.slam_reset import (
    SLAM_PROCESS_PATTERN,
    find_slam_pids,
    reset_slam_process,
)


def test_찾은_모든_slam_에_TERM_을_보내고_True():
    sent = []
    ok = reset_slam_process(
        killer=lambda pid, sig: sent.append((pid, sig)),
        finder=lambda: [111, 222],
    )
    assert ok is True
    assert sent == [(111, signal.SIGTERM), (222, signal.SIGTERM)]


def test_slam_이_없으면_False():
    # enable_slam 없는 구성에서 정상 — 호출부는 경고만 남기고 진행한다.
    assert reset_slam_process(killer=lambda p, s: None, finder=list) is False


def test_이미_사라진_프로세스는_삼킨다():
    def killer(pid, sig):
        raise ProcessLookupError

    assert reset_slam_process(killer=killer, finder=lambda: [999]) is False


def test_일부만_실패해도_보낸게_있으면_True():
    def killer(pid, sig):
        if pid == 111:
            raise ProcessLookupError

    assert reset_slam_process(killer=killer, finder=lambda: [111, 222]) is True


def test_자기_자신은_절대_고르지_않는다(tmp_path):
    """pkill -f 시절의 실제 사고를 못박는다 (2026-08-09).

    `pkill -f async_slam_toolbox_node` 는 그 문자열을 인자로 들고 있는
    호출자까지 죽였다(exit 144). mission_manager 가 자기를 죽이면 임무 시작이
    스택을 내리는 사고가 된다.
    """
    me = os.getpid()
    (tmp_path / str(me)).mkdir()
    # 자기 cmdline 에 패턴이 들어 있어도(= 그 시절의 사고 조건) 고르면 안 된다.
    (tmp_path / str(me) / 'cmdline').write_bytes(
        f'python -c import {SLAM_PROCESS_PATTERN}'.encode()
    )
    assert me not in find_slam_pids(proc_root=str(tmp_path))


def test_패턴이_들어간_남의_프로세스는_고른다(tmp_path):
    other = os.getpid() + 90001   # 존재하지 않을 만한 번호 — 계보에 없다
    (tmp_path / str(other)).mkdir()
    (tmp_path / str(other) / 'cmdline').write_bytes(
        f'/opt/ros/humble/lib/slam_toolbox/{SLAM_PROCESS_PATTERN}\x00--ros-args'.encode()
    )
    assert find_slam_pids(proc_root=str(tmp_path)) == [other]


def test_패턴이_없으면_안_고른다(tmp_path):
    other = os.getpid() + 90002
    (tmp_path / str(other)).mkdir()
    (tmp_path / str(other) / 'cmdline').write_bytes(b'/usr/bin/python3\x00mission_manager')
    assert find_slam_pids(proc_root=str(tmp_path)) == []

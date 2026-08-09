"""임무 시작 시 slam_toolbox 재시작 = 지도 초기화 (S15P11A301-362).

Humble slam_toolbox 엔 지도 리셋 서비스가 없다(서비스 목록 실측 — clear_changes
는 마지막 최적화 이전으로 되돌릴 뿐 지도를 비우지 않는다). 그래서 프로세스를
내리고 launch 의 respawn(slam.launch.py)이 빈 지도로 되살리는 방식을 쓴다.

SIGTERM 을 쓴다 — slam_toolbox 는 SIGTERM 에 정상 종료하고, respawn 은 종료
사유와 무관하게 되살린다. 재시작 공백(3~4초: respawn 1초 + 첫 스캔 즉시 +
map_update_interval 2초) 동안 map frame 이 사라지는데, 탐사는 pose(TF)가 없으면
목표를 보내지 않으므로 별도 대기 코드가 필요 없다.

## pkill 을 쓰지 않는다 (2026-08-09 실측)

종전 구현은 `pkill -f async_slam_toolbox_node` 였는데, 검증 중에 **호출한 셸까지
같이 죽었다**(exit 144 = SIGTERM). `pkill -f` 는 전체 cmdline 을 훑으므로 그
문자열을 인자로 들고 있는 프로세스 — 즉 이 코드를 실행하는 쪽 — 도 일치한다.
mission_manager 가 이걸 부르면 자기가 죽을 수 있고, 그러면 임무 시작이 스택
일부를 내리는 사고가 된다.

그래서 /proc 을 직접 훑어 **PID 단위로 고르고, 자기 자신과 조상은 제외한다.**
표준 라이브러리만 쓰므로 시험이 가볍다.
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

#: launch 가 띄우는 slam 실행 파일 이름.
SLAM_PROCESS_PATTERN = 'async_slam_toolbox_node'


def _own_lineage() -> set[int]:
    """자기 자신과 조상 PID 들. 여기 속한 프로세스에는 신호를 보내지 않는다."""
    lineage: set[int] = set()
    pid = os.getpid()
    while pid > 1 and pid not in lineage:
        lineage.add(pid)
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            # comm 에 공백·괄호가 있을 수 있어 마지막 ')' 뒤부터 파싱한다.
            pid = int(stat[stat.rindex(')') + 1:].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return lineage


def find_slam_pids(proc_root: str = '/proc') -> list[int]:
    """cmdline 에 slam 실행 파일 이름이 있는 PID. 자기 계보는 뺀다."""
    skip = _own_lineage()
    found: list[int] = []
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid in skip:
            continue
        try:
            cmdline = Path(f'{proc_root}/{entry}/cmdline').read_bytes()
        except OSError:
            continue  # 훑는 사이에 사라진 프로세스 — 정상이다
        if SLAM_PROCESS_PATTERN.encode() in cmdline:
            found.append(pid)
    return found


def reset_slam_process(killer=os.kill, finder=find_slam_pids) -> bool:
    """slam 프로세스에 SIGTERM 을 보낸다. 하나라도 보냈으면 True.

    False 는 「slam 이 떠 있지 않다」이다 — enable_slam 없이 도는 구성(무지도
    검증 등)에서 정상이므로 호출부는 경고만 남기고 진행한다.
    """
    sent = False
    for pid in finder():
        try:
            killer(pid, signal.SIGTERM)
            sent = True
        except (ProcessLookupError, PermissionError):
            continue
    return sent

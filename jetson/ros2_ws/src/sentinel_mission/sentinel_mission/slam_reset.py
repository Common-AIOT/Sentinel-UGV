"""임무 시작 시 slam_toolbox 재시작 = 지도 초기화 (S15P11A301-362).

Humble slam_toolbox 엔 지도 리셋 서비스가 없다(서비스 목록 실측 — clear_changes
는 마지막 최적화 이전으로 되돌릴 뿐 지도를 비우지 않는다). 그래서 프로세스를
내리고 launch 의 respawn(slam.launch.py)이 빈 지도로 되살리는 방식을 쓴다.

SIGTERM 을 쓴다 — slam_toolbox 는 SIGTERM 에 정상 종료하고, respawn 은 종료
사유와 무관하게 되살린다. 재시작 공백(3~4초: respawn 1초 + 첫 스캔 즉시 +
map_update_interval 2초) 동안 map frame 이 사라지는데, 탐사는 pose(TF)가 없으면
목표를 보내지 않으므로 별도 대기 코드가 필요 없다.

mission_manager 노드에서 분리한 이유: 프로세스 신호는 ROS 와 무관한 순수
파이썬이라 rclpy 없이 시험할 수 있다.
"""

from __future__ import annotations

import subprocess

#: launch 가 띄우는 slam 실행 파일 이름. 이 패턴은 mission_manager 자신의
#: cmdline 과 절대 겹치지 않아야 한다 — pkill -f 는 전체 cmdline 을 본다.
SLAM_PROCESS_PATTERN = 'async_slam_toolbox_node'


def reset_slam_process(runner=subprocess.run) -> bool:
    """slam 프로세스에 SIGTERM 을 보낸다. 신호가 나갔으면 True.

    False 는 「slam 이 떠 있지 않다」(pkill 종료코드 1)이다 — enable_slam 없이
    도는 구성(무지도 검증 등)에서 정상이므로 호출부는 경고만 남기고 진행한다.
    """
    try:
        result = runner(
            ['pkill', '-TERM', '-f', SLAM_PROCESS_PATTERN],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0

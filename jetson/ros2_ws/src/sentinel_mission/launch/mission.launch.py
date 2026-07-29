"""mission_manager 실행 (S15P11A301-133).

`recording_manager`와 함께 띄우지 않는다. 26.1의 단일 권한은 발행자를 하나로
두는 것이고, 두 노드의 수명은 별개다. 녹화가 죽어도 임무 상태는 유지돼야 한다.
"""

from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description() -> LaunchDescription:
    config = os.path.join(
        get_package_share_directory('sentinel_mission'), 'config', 'mission.yaml'
    )
    return LaunchDescription(
        [
            Node(
                package='sentinel_mission',
                executable='mission_manager',
                name='mission_manager',
                output='screen',
                parameters=[config],
            )
        ]
    )

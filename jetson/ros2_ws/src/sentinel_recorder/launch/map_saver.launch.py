"""지도 저장 노드를 띄운다 (S15P11A301-171).

    ros2 launch sentinel_recorder map_saver.launch.py

`demo.launch.py`가 recorder 단계에서 함께 include한다. 단독으로 띄울 때는 SLAM이
먼저 떠 있어야 한다 — `save_map` 서비스가 slam_toolbox에 있다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    share = get_package_share_directory('sentinel_recorder')
    default_params = os.path.join(share, 'config', 'map_saver.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        Node(
            package='sentinel_recorder',
            executable='map_saver',
            name='map_saver',
            output='screen',
            parameters=[LaunchConfiguration('params_file')],
        ),
    ])

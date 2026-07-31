"""지도 저장·업로드 노드를 띄운다 (S15P11A301-171).

    ros2 launch sentinel_recorder map_saver.launch.py

`demo.launch.py`가 recorder 단계에서 함께 include한다. 단독으로 띄울 때는 SLAM이
먼저 떠 있어야 한다 — `save_map` 서비스가 slam_toolbox에 있다.

두 노드를 함께 띄우지만 서로 독립이다(32장 장애 격리). 업로드가 망 때문에
막혀도 저장은 계속되고, 업로더가 죽어도 파일은 디스크에 남아 다음 기동에서
이어받는다. `enable_upload:=false`로 업로더만 뺄 수 있다 — 백엔드 없이
저장만 확인할 때 쓴다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    share = get_package_share_directory('sentinel_recorder')
    default_params = os.path.join(share, 'config', 'map_saver.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        DeclareLaunchArgument('enable_upload', default_value='true'),
        Node(
            package='sentinel_recorder',
            executable='map_saver',
            name='map_saver',
            output='screen',
            parameters=[LaunchConfiguration('params_file')],
        ),
        Node(
            package='sentinel_recorder',
            executable='map_uploader',
            name='map_uploader',
            output='screen',
            parameters=[LaunchConfiguration('params_file')],
            condition=IfCondition(LaunchConfiguration('enable_upload')),
        ),
    ])

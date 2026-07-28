"""recording_manager를 띄운다 (S15P11A301-123).

    ros2 launch sentinel_recorder recorder.launch.py

링 writer는 sentinel_streaming이 띄운다. 이 노드는 index.json만 읽으므로
스트리밍보다 먼저 떠도 되고 나중에 떠도 된다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('sentinel_recorder')
    default_params = os.path.join(share, 'config', 'recorder.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        DeclareLaunchArgument('buffer_directory', default_value=''),
        DeclareLaunchArgument('pending_directory', default_value=''),

        Node(
            package='sentinel_recorder',
            executable='recording_manager',
            name='recording_manager',
            output='screen',
            parameters=[LaunchConfiguration('params_file')],
        ),
    ])

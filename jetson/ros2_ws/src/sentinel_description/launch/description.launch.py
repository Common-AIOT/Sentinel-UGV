import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    urdf_path = os.path.join(
        get_package_share_directory('sentinel_description'), 'urdf', 'sentinel.urdf')
    with open(urdf_path, encoding='utf-8') as f:
        robot_description = f.read()
    return LaunchDescription([
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             name='robot_state_publisher', output='screen',
             parameters=[{'robot_description': robot_description}])
    ])

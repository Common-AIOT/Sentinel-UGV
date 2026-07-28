"""cloud_bridge 노드를 띄운다 (S15P11A301-128).

브로커 주소와 자격증명은 커밋하지 않으므로 launch 인자로 넘긴다.

    ros2 launch sentinel_bridge cloud_bridge.launch.py \
        broker_host:=mqtt.sentinel-ugv.xyz broker_username:=sentinel-01
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('sentinel_bridge')
    default_params = os.path.join(share, 'config', 'communication.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        DeclareLaunchArgument('broker_host', default_value=''),
        DeclareLaunchArgument('broker_port', default_value=''),
        DeclareLaunchArgument('broker_username', default_value=''),
        DeclareLaunchArgument('broker_password', default_value=''),
        DeclareLaunchArgument('tls_ca_certs', default_value=''),
        DeclareLaunchArgument('tls_insecure', default_value=''),

        Node(
            package='sentinel_bridge',
            executable='cloud_bridge',
            name='cloud_bridge',
            output='screen',
            parameters=[
                LaunchConfiguration('params_file'),
                # 빈 문자열은 yaml 값을 덮지 않게 아래에서 걸러야 하지만,
                # launch가 조건부 파라미터를 지원하지 않아 여기서는 항상 넘긴다.
                # 빈 값이면 노드가 None으로 해석하므로 결과가 같다.
                {
                    'broker_host': LaunchConfiguration('broker_host'),
                    'broker_username': LaunchConfiguration('broker_username'),
                    'broker_password': LaunchConfiguration('broker_password'),
                    'tls_ca_certs': LaunchConfiguration('tls_ca_certs'),
                },
            ],
        ),
    ])

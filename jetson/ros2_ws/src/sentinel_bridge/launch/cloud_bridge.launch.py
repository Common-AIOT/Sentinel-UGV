"""cloud_bridge 노드를 띄운다 (S15P11A301-128).

브로커 주소와 자격증명은 커밋하지 않으므로 launch 인자로 넘긴다.

    ros2 launch sentinel_bridge cloud_bridge.launch.py \\
        broker_host:=mqtt.sentinel-ugv.xyz broker_username:=sentinel-01

빈 인자는 파라미터로 넘기지 않는다 (S15P11A301-156). 전에는 빈 문자열을 항상
넘기며 "빈 값이면 노드가 None으로 해석하므로 결과가 같다"고 적어 뒀는데,
`broker_host`에서 틀렸다 — 빈 문자열이 communication.yaml의 host를 덮어쓰고
paho가 `ValueError: Invalid host.`로 죽었다. demo.launch가 `broker_password`만
넘기는 순간 드러났다. 그래서 값이 있는 인자만 골라 넘긴다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

OVERRIDABLE = (
    'broker_host',
    'broker_port',
    'broker_username',
    'broker_password',
    'tls_ca_certs',
    'tls_insecure',
)


def _make_node(context):
    """인자를 실제 값으로 풀어 비어 있지 않은 것만 파라미터로 만든다.

    `OpaqueFunction`을 쓰는 이유는 launch의 선언적 API로는 "값이 있을 때만
    파라미터에 포함"을 표현할 수 없기 때문이다.
    """
    overrides = {}
    for name in OVERRIDABLE:
        value = LaunchConfiguration(name).perform(context)
        if value != '':
            overrides[name] = value
    if 'broker_port' in overrides:
        overrides['broker_port'] = int(overrides['broker_port'])
    if 'tls_insecure' in overrides:
        overrides['tls_insecure'] = overrides['tls_insecure'] == 'true'

    parameters = [LaunchConfiguration('params_file')]
    if overrides:
        parameters.append(overrides)
    return [
        Node(
            package='sentinel_bridge',
            executable='cloud_bridge',
            name='cloud_bridge',
            output='screen',
            parameters=parameters,
        ),
    ]


def generate_launch_description():
    share = get_package_share_directory('sentinel_bridge')
    default_params = os.path.join(share, 'config', 'communication.yaml')

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        *[DeclareLaunchArgument(name, default_value='') for name in OVERRIDABLE],
        OpaqueFunction(function=_make_node),
    ])

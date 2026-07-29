"""SLAM 실행 (S15P11A301-137, 명세 23.1·23.2).

`slam_toolbox`가 `/scan`으로 지도를 만들고 `map → odom` TF를 발행한다. 그러면
`cloud_bridge`가 `map → base_footprint`를 조회해 telemetry의 `pose`를 채운다.

## 오도메트리가 없다

엔코더가 ESP32 연동(S15P11A301-84·85) 이후이므로 `ekf_node`의 `/odometry/filtered`가
없다. 그런데 `slam_toolbox`는 `odom → base_frame` TF를 **요구한다.** 없으면
"Failed to compute odom pose"를 반복하고 지도를 만들지 않는다.

그래서 `odom → base_footprint`를 static identity로 발행한다. 그러면 로봇의 실제
이동이 전부 `map → odom`에 담긴다. 스캔 매칭만으로 위치를 추정하는 구성이다.

**이것은 임시 구성이다.** 두 가지 한계가 있다.

첫째, 제자리 회전에서 정확도가 떨어진다. 스캔 매칭은 벽 모양이 비슷한 방향을
구분하지 못하는데, 오도메트리가 있으면 회전량을 초기 추정으로 줄 수 있다.

둘째, `odom`이 로봇 이동을 표현하지 않는다. Nav2가 붙으면 `odom`을 속도 추정에
쓰는데 그 값이 항상 0이 된다. 엔코더가 붙으면 이 static TF를 걷어내고
`/odometry/filtered`를 쓴다.

## 센서와 따로 띄운다

`sensors.launch.py`에 넣지 않는다. SLAM을 끄고도 카메라·라이다·스트리밍이 돌아야
하고(32장 장애 격리), SLAM은 메모리를 계속 쓰므로 필요할 때만 올린다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    config = os.path.join(
        get_package_share_directory('sentinel_bringup'),
        'config',
        'slam_toolbox.yaml',
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'publish_static_odom',
                default_value='true',
                description=(
                    '오도메트리가 없는 동안 odom→base_footprint를 identity로 '
                    '발행한다. ekf_node가 붙으면 false로 둔다'
                ),
            ),
            DeclareLaunchArgument(
                'use_sim_time', default_value='false'
            ),
            # slam_toolbox가 요구하는 odom TF를 채운다. 실제 이동은 map→odom이
            # 담으므로 이 값은 항상 identity다.
            #
            # ekf_node가 붙으면 이 노드와 slam_toolbox가 같은 TF를 발행해 충돌한다.
            # 그때 publish_static_odom:=false 로 끈다.
            Node(
                package='tf2_ros',
                executable='static_transform_publisher',
                name='odom_to_base_footprint_static',
                arguments=[
                    '--x', '0', '--y', '0', '--z', '0',
                    '--roll', '0', '--pitch', '0', '--yaw', '0',
                    '--frame-id', 'odom',
                    '--child-frame-id', 'base_footprint',
                ],
                condition=IfCondition(LaunchConfiguration('publish_static_odom')),
                output='screen',
            ),
            Node(
                package='slam_toolbox',
                executable='async_slam_toolbox_node',
                name='slam_toolbox',
                output='screen',
                parameters=[
                    config,
                    {'use_sim_time': LaunchConfiguration('use_sim_time')},
                ],
            ),
        ]
    )

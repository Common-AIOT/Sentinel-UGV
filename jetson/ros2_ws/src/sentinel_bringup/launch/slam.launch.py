"""SLAM 실행 (S15P11A301-137, 명세 23.1·23.2).

`slam_toolbox`가 `/scan`으로 지도를 만들고 `map → odom` TF를 발행한다. 그러면
`cloud_bridge`가 `map → base_footprint`를 조회해 telemetry의 `pose`를 채운다.

## odom → base_footprint 를 누가 발행하는가

`slam_toolbox`는 `odom → base_frame` TF를 **요구한다.** 없으면 "Failed to compute
odom pose"를 반복하고 지도를 만들지 않는다.

그 TF를 낼 수 있는 곳이 둘이다.

* `esp32_bridge`의 휠 오도메트리 (S15P11A301-174). 엔코더 50Hz 실측이다.
* 이 파일의 static identity (`publish_static_odom`). 오도메트리가 없을 때의 대체다.

**둘 중 정확히 하나만 켜져야 한다.** 둘 다 켜지면 같은 TF를 다투어 위치가 흔들리고,
둘 다 꺼지면 지도가 아예 만들어지지 않는다. 어느 쪽이든 증상이 "지도가 이상하다"로만
보여 원인을 찾기 어렵다.

그래서 `demo.launch.py`가 `publish_static_odom`을 `enable_esp32`의 **부정**으로
넘긴다(S15P11A301-222). 규약이 아니라 구조로 묶여 있으므로 두 값이 어긋날 수 없다.
이 파일을 단독으로 띄우면 기본값 `true`라 예전처럼 identity로 돈다.

### static identity 로 돌 때의 한계

첫째, 제자리 회전에서 정확도가 떨어진다. 스캔 매칭은 벽 모양이 비슷한 방향을
구분하지 못하는데, 오도메트리가 있으면 회전량을 초기 추정으로 줄 수 있다.

둘째, `odom`이 로봇 이동을 표현하지 않는다. Nav2가 붙으면 `odom`을 속도 추정에
쓰는데 그 값이 항상 0이 된다.

셋째, `slam_toolbox.yaml`의 `minimum_travel_distance`를 0으로 둬야 한다. 이동량을
odom에서 읽으므로 identity면 매 스캔이 "제자리"로 판정돼 버려진다. 그 파일의 주석에
실측 기록이 있다.

`ekf_node`(robot_localization)로 IMU와 융합하는 것은 다음 단계다. IMU 모델이
TBD-HW-012에서 미확정이므로 지금은 휠 오도메트리만 쓴다. 그때는 `ekf_node`가
이 TF를 소유하고 `esp32_bridge`의 `publish_odom_tf`도 false로 돌아간다.

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

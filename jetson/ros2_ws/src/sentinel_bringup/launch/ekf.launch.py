"""엔코더·IMU EKF 융합 (S15P11A301-236, 명세 23.1~23.2).

`robot_localization` 의 `ekf_node` 가 휠 오도메트리 vx 와 IMU yaw 각속도를 융합해
`odom → base_footprint` TF 를 낸다. 파라미터는 `config/ekf.yaml` 한 곳이다.

## 이 TF 를 낼 수 있는 곳이 셋이다

`esp32_sensor_bridge`(휠 오도메트리), `slam.launch.py` 의 static identity,
그리고 이 노드다. **정확히 하나만 켜져야 한다** — 둘 이상이면 같은 TF 를 다투어
위치가 흔들리고, 하나도 없으면 `slam_toolbox` 가 지도를 아예 만들지 않는다. 어느
쪽이든 증상이 "지도가 이상하다" 로만 보인다.

배타 처리는 `demo.launch.py` 가 구조로 한다(S15P11A301-222 의 2자 배타를 3자로
확장). 이 파일을 단독으로 띄우면 다른 발행자와 충돌할 수 있으므로, 단독 검증 시엔
`esp32_bridge` 의 `publish_odom_tf:=false` 를 확인한다.

## 왜 IMU 없이는 못 켜는가

EKF 입력이 `/wheel/odometry`(vx)와 `/imu/data_raw`(vyaw) 둘이다. ESP32 센서 보드가
없으면 둘 다 오지 않아 EKF 가 예측만으로 0 을 내고, 그것이 TF 로 나가면 로봇이
영원히 원점에 있는 것으로 보인다. 그래서 `demo.launch.py` 는 `enable_ekf` 를
`enable_esp32` 와 AND 로 묶는다.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument(
            'ekf_params',
            default_value=PathJoinSubstitution(
                [FindPackageShare('sentinel_bringup'), 'config', 'ekf.yaml']
            ),
        ),
        Node(
            package='robot_localization',
            executable='ekf_node',
            name='ekf_filter_node',
            output='screen',
            parameters=[LaunchConfiguration('ekf_params')],
            # 기본 출력 토픽이 `/odometry/filtered` 다(8.2 노드 표와 같다).
            # remap 하지 않는다 — Nav2 의 odom_topic 을 그쪽으로 옮기는 것은
            # S15P11A301-249 자원 계측 뒤 판단이다. 지금은 두 토픽이 공존한다.
        ),
    ])

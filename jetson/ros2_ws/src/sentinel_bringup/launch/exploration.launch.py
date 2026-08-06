"""Frontier 탐사 (S15P11A301-172, 명세 23.3~23.5).

    /map (slam_toolbox)        ─┐
    /mission/status (26.2)     ─┤→ exploration → NavigateToPose (Nav2)
    map→base_footprint (TF)    ─┘                     ↓
                                              /cmd_vel_nav → 안전 체인(24.1)
                                              → /cmd_vel → vehicle_kinematics → 바퀴

## 이 launch 는 혼자 켜면 아무것도 하지 않는다

세 가지가 함께 있어야 로봇이 움직인다.

    enable_slam        /map 과 map→base_footprint TF. 없으면 WAIT_MAP 에서 멈춘다
    enable_nav2        NavigateToPose 액션 서버. 없으면 목표가 UNAVAILABLE 로 돌아온다
    enable_safety      /cmd_vel_nav → /cmd_vel 체인. 없으면 Nav2 가 내도 바퀴에 안 닿는다

`demo.launch.py` 가 `enable_exploration:=true` 에서 Nav2 를 함께 켜지만
`enable_safety` 는 켜지 않는다 — 그것이 실제로 모터를 돌리는 스위치이므로 사람이
따로 켠다(S15P11A301-298 의 경고).

## navigator=nav2 를 여기서 준다

노드 기본값은 `null`(목표만 로그)이다. 이 launch 로 뜬 것만 실제 주행하게 해서,
탐사 노드가 다른 경로로 켜졌을 때 예상 밖에 모터가 도는 일을 막는다.

## 움직이지 않을 때 보는 순서

`~/status` 의 `state` 가 어디서 멈췄는지 말해 준다.

    HOLD        movementAllowed=false. 임무가 EXPLORING 이 아니거나 상태가 낡았다
    WAIT_MAP    /map 을 아직 못 받았다. slam_toolbox 와 QoS(latched) 확인
    DONE        후보가 없다. blockedGoals 가 크면 「다 봤다」가 아니라 「못 갔다」다
    DRIVING     목표를 보냈다. 여기서 바퀴가 안 돌면 아래(Nav2·안전 체인·운동학)다
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument(
            'navigator',
            default_value='nav2',
            description=(
                'nav2 | null. null 은 목표만 로그로 남기고 주행하지 않는다 — '
                '선택 로직만 확인할 때 쓴다.'
            ),
        ),
        DeclareLaunchArgument(
            'max_radius_m',
            default_value='12.0',
            description='home 에서 이 반경 밖 frontier 는 후보에서 뺀다. 시연장 실측으로 좁힌다.',
        ),
        Node(
            package='sentinel_exploration',
            executable='exploration',
            name='exploration',
            output='screen',
            parameters=[{
                'navigator': LaunchConfiguration('navigator'),
                'max_radius_m': LaunchConfiguration('max_radius_m'),
            }],
        ),
    ])

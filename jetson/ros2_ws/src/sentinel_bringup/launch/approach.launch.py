"""사람 접근 주행 (S15P11A301-247, 명세 25.3·24.2~24.3).

    /perception/person_candidates ─┐
    /scan                         ─┤→ approach → /cmd_vel_nav → 안전 체인(24.1)
    /mission/status (26.2)        ─┘                → /cmd_vel → vehicle_kinematics

## Nav2 가 없어도 동작한다

이것이 exploration 과 다른 점이다. 탐지 노드가 `position` 을 채우지 못하므로
(`ai/detection/src/candidates.py` 가 항상 `null` 을 낸다) 지도 좌표 목표를 만들 수
없고, 그래서 카메라 방위각 + 그 방향 LiDAR 거리만 쓰는 **bearing-only** 로 간다.
S15P11A301-247 이 「bearing-only 는 Nav2 없이 동작하므로 그것부터 만들 수 있다」고
적어 둔 경로다.

필요한 것은 둘뿐이다.

    enable_detector    /perception/person_candidates. 없으면 접근할 대상이 없다
    enable_safety      /cmd_vel_nav → /cmd_vel 체인. 없으면 명령이 바퀴에 안 닿는다

`/scan` 은 라이다(`enable_sensors`)에서 온다. 없으면 거리를 모르므로 **멈춘 채로
있는다** — 모르는 것과 먼 것을 섞지 않는 것이 이 노드의 규칙이다.

## 제자리 회전을 하지 않는다

티켓 본문의 「방위 정렬(제자리 회전) 후 전진」은 차동 구동 전제의 낡은 서술이다.
2026-08-06 전륜 서보 조향으로 바뀌면서 제자리 회전이 불가능해졌고
(`vehicle_kinematics` 가 `v≈0·ω≠0` 을 거부한다, §34-2), 접근은 **전진하며 조향하는
호**다. 그 대가로 곡률이 `1/R_min` 으로 제한된다 — 근거는 `approach.py` 에 있다.

## 움직이지 않을 때 보는 순서

    1. /mission/status 의 state 가 PERSON_APPROACHING 인가, movementAllowed 가 true 인가
    2. /perception/person_candidates 에 box 가 실려 오는가 (position 은 null 이어도 된다)
    3. /scan 이 그 방위에 유효 거리를 주는가 — 없으면 no_range 로 멈춘다
    4. 그다음은 안전 체인이다(/safety/gate_state 의 reasons)
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument(
            'stop_distance_m',
            default_value='0.60',
            description=(
                '이 거리에서 도착으로 보고 SAFE_POSE_REACHED 를 낸다. '
                'collision_monitor 정지 구역(0.40m)보다 커야 한다 — 작으면 안전 체인이 '
                '먼저 속도를 0 으로 만들어 도착 선언이 나오지 않는다.'
            ),
        ),
        DeclareLaunchArgument(
            'max_speed_mps',
            default_value='0.10',
            description='24.2 피해자 접근 상한. 안전 체인과 함께 지킨다.',
        ),
        DeclareLaunchArgument(
            'camera_hfov_deg',
            default_value='52.0',
            description=(
                'BRIO 100 대각 58° 의 수평 환산(실측 전 잠정). '
                'exploration 과 같은 값이어야 한다 — 다르면 목표와 접근이 어긋난다.'
            ),
        ),
        Node(
            package='sentinel_approach',
            executable='approach',
            name='approach',
            output='screen',
            parameters=[{
                'stop_distance_m': LaunchConfiguration('stop_distance_m'),
                'max_speed_mps': LaunchConfiguration('max_speed_mps'),
                'camera_hfov_deg': LaunchConfiguration('camera_hfov_deg'),
            }],
        ),
    ])

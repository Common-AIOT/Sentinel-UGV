"""Nav2 스택 (S15P11A301-235, 명세 24.1).

planner(NavFn) + controller(rotation shim → RPP) + behaviors + bt_navigator +
waypoint_follower 를 lifecycle_manager 로 띄운다. 파라미터는 `config/nav2.yaml`
한 곳이다.

## cmd_vel 은 /cmd_vel_nav 로 나간다

24.1 체인은 Nav2 출력이 안전 체인(collision_monitor → command_mux → …,
S15P11A301-237)을 거쳐 `/cmd_vel` 이 되기를 요구한다. 그래서 기본 remap 이
`/cmd_vel_nav` 다 — 체인이 아직 없으므로 **기본 구성에서는 이 토픽을 아무도
소비하지 않고, 로봇은 움직이지 않는다.** 그것이 의도다.

체인 없이 단독 검증할 때만 `nav2_cmd_vel_topic:=/cmd_vel` 로 띄워
`vehicle_kinematics`(S15P11A301-234)에 직결한다. 시연 기본 구성으로 삼지
않는다 — 안전 체인을 우회한 채 굳어지는 것을 막기 위해서다.

## 검증 (Nav2 없이 되는 것 / 안 되는 것)

로봇이 정지 상태(static odom)여도 계획 계층은 전부 검증된다 — 지도가 있으니
`ComputePathToPose` 가 경로를 내고, 벽·미지 목표는 거부한다. 추종 계층은 실차가
있어야 한다(오도메트리가 0 이라 progress checker 가 정체로 판정한다).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    params_file = LaunchConfiguration('nav2_params')
    cmd_vel_topic = LaunchConfiguration('nav2_cmd_vel_topic')

    # cmd_vel 을 내는 노드는 controller 와 behaviors(spin·backup) 둘이다.
    # 한쪽만 remap 하면 recovery 회전이 안전 체인을 우회한다.
    cmd_vel_remap = [('cmd_vel', cmd_vel_topic)]

    return LaunchDescription([
        DeclareLaunchArgument(
            'nav2_params',
            default_value=PathJoinSubstitution(
                [FindPackageShare('sentinel_bringup'), 'config', 'nav2.yaml']
            ),
        ),
        DeclareLaunchArgument(
            'nav2_cmd_vel_topic', default_value='/cmd_vel_nav',
            description='기본은 안전 체인 입력(24.1). 체인 없이 단독 검증할 때만 /cmd_vel',
        ),

        Node(
            package='nav2_planner', executable='planner_server',
            name='planner_server', output='screen',
            parameters=[params_file],
        ),
        Node(
            package='nav2_controller', executable='controller_server',
            name='controller_server', output='screen',
            parameters=[params_file],
            remappings=cmd_vel_remap,
        ),
        Node(
            package='nav2_behaviors', executable='behavior_server',
            name='behavior_server', output='screen',
            parameters=[params_file],
            remappings=cmd_vel_remap,
        ),
        Node(
            package='nav2_bt_navigator', executable='bt_navigator',
            name='bt_navigator', output='screen',
            # 전륜 조향용 복구 트리 (S15P11A301-172). **경로는 여기서 넘긴다** —
            # params YAML 안의 `$(find-pkg-share ...)` 는 launch 치환이라 파일에서는
            # 문자열 그대로 전달되고, bt_navigator 가 그 이름의 파일을 못 열어
            # 활성화에 실패한다(2026-08-07 실측). 두 트리를 모두 지정해야 한다 —
            # 하나만 고치면 나머지 하나가 여전히 Spin 을 부른다.
            parameters=[params_file, {
                'default_nav_to_pose_bt_xml': PathJoinSubstitution([
                    FindPackageShare('sentinel_bringup'),
                    'behavior_trees', 'navigate_to_pose_ackermann.xml',
                ]),
                'default_nav_through_poses_bt_xml': PathJoinSubstitution([
                    FindPackageShare('sentinel_bringup'),
                    'behavior_trees', 'navigate_through_poses_ackermann.xml',
                ]),
            }],
        ),
        Node(
            package='nav2_waypoint_follower', executable='waypoint_follower',
            name='waypoint_follower', output='screen',
            parameters=[params_file],
        ),
        Node(
            package='nav2_lifecycle_manager', executable='lifecycle_manager',
            name='lifecycle_manager_navigation', output='screen',
            parameters=[{
                'use_sim_time': False,
                'autostart': True,
                'node_names': [
                    'planner_server',
                    'controller_server',
                    'behavior_server',
                    'bt_navigator',
                    'waypoint_follower',
                ],
            }],
        ),
    ])

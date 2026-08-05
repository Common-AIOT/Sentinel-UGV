"""주행 안전 체인 (S15P11A301-237, 명세 24.1·34-7).

자율·수동 어느 쪽에서 온 속도든 이 네 층을 거쳐야 바퀴에 닿는다.

    /cmd_vel_nav ─┐
                  ├→ command_mux       → /cmd_vel_muxed
    /cmd_vel_manual┘
      → velocity_smoother  → /cmd_vel_smoothed
      → collision_monitor  → /cmd_vel_safe
      → safety_gate        → /cmd_vel  → vehicle_kinematics(S15P11A301-234)

## 이 launch 가 없으면 바퀴가 돌지 않는다

Nav2(S15P11A301-235)는 `/cmd_vel_nav` 로 내고 `vehicle_kinematics`(234)는
`/cmd_vel` 을 구독한다. 둘 다 머지돼 있는데 **이어 주는 것이 없어서** 지금까지
자율 주행이 한 바퀴도 돌지 않았다. 이 파일이 그 연결이다.

## 가운데 두 층은 nav2 것이다

`velocity_smoother`·`collision_monitor` 는 만들지 않고 파라미터만 준다
(`config/safety.yaml`). 양 끝단만 우리 것이다 — `command_mux` 는 `controlMode` 를
읽어야 하고 `safety_gate` 는 임무 상태·초음파·`/scan` 침묵을 봐야 해서 이 프로젝트
고유 판정이 들어간다.

## 순서를 바꾸지 말 것

`smoother` 가 `collision_monitor` **앞**이다. 뒤에 두면 급정지가 감가속 제한에
걸려 천천히 멈춘다. 04장 8.1 요약이 반대 순서로 적혀 있었고 그것을 이 티켓에서
정정했다(34-7 이 규범).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, TimerAction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    params = LaunchConfiguration('safety_params')

    return LaunchDescription([
        DeclareLaunchArgument(
            'safety_params',
            default_value=PathJoinSubstitution(
                [FindPackageShare('sentinel_bringup'), 'config', 'safety.yaml']
            ),
        ),

        # ── 1층: 자율/수동 중재 ─────────────────────────────────────────────
        Node(
            package='sentinel_safety',
            executable='command_mux',
            name='command_mux',
            output='screen',
            parameters=[{'output_topic': '/cmd_vel_muxed'}],
        ),

        # ── 2층: 가감속·최대 속도 제한 ──────────────────────────────────────
        # 입출력 토픽은 파라미터가 아니라 **remap** 이다(nav2_velocity_smoother 는
        # `cmd_vel`·`cmd_vel_smoothed` 라는 고정 이름을 쓴다).
        Node(
            package='nav2_velocity_smoother',
            executable='velocity_smoother',
            name='velocity_smoother',
            output='screen',
            parameters=[params],
            remappings=[
                ('cmd_vel', '/cmd_vel_muxed'),
                ('cmd_vel_smoothed', '/cmd_vel_smoothed'),
            ],
        ),

        # ── 3층: 근거리 충돌 방지 ───────────────────────────────────────────
        # 이쪽은 반대로 토픽이 **파라미터**다(cmd_vel_in_topic·cmd_vel_out_topic,
        # safety.yaml 에 있다). 같은 스택인데 방식이 달라서 헷갈리기 쉽다.
        Node(
            package='nav2_collision_monitor',
            executable='collision_monitor',
            name='collision_monitor',
            output='screen',
            parameters=[params],
        ),

        # nav2 의 두 층은 **lifecycle 노드**다. 띄우기만 하면
        # "Waiting on external lifecycle transitions to activate" 로 멈춰 있고,
        # 그 상태에서는 입력을 받아도 아무것도 내지 않는다 — 체인이 가운데서
        # 끊긴다. 실측으로 확인했다(2026-08-04: 두 노드 `unconfigured`, 그때
        # /cmd_vel_muxed 20.02Hz·/cmd_vel 19.96Hz 인데 사이 두 토픽은 침묵).
        #
        # 관리자 이름은 nav2.launch.py 의 `lifecycle_manager_navigation` 과
        # **달라야 한다.** 같은 이름이면 두 관리자가 같은 서비스 이름을 다투고,
        # 그때 어느 쪽이 이기는지는 기동 순서에 달린다.
        #
        # **3초 늦게 띄운다.** 다섯을 동시에 올렸을 때 전이 요청이 경합에서
        # 졌다(2026-08-04 실측):
        #
        #   velocity_smoother: Configuring velocity smoother
        #   velocity_smoother.rclcpp: failed to send response to
        #     /velocity_smoother/change_state (timeout)
        #
        # 노드는 configure 를 끝냈는데 **응답이 시각 안에 못 갔다.** 관리자는
        # 실패로 보고 activate 를 보내지 않으므로 `inactive` 에서 멈추고,
        # 그 상태에서는 입력을 받아도 아무것도 내지 않는다. 같은 기동에서
        # collision_monitor 는 `active` 였다 — 순서 운에 달린 경합이다.
        #
        # 원인은 CPU 포화다(로드 14/6코어, S15P11A301-249). 지연으로 노드가
        # 서비스를 완전히 준비한 뒤 전이를 시작하게 해 경합 자체를 없앤다.
        # 그래도 더 부하가 높으면 다시 질 수 있으므로, 기동 후 확인이 필요하다:
        #
        #   ros2 lifecycle get /velocity_smoother   → active [3] 이어야 한다
        TimerAction(period=3.0, actions=[
            Node(
                package='nav2_lifecycle_manager', executable='lifecycle_manager',
                name='lifecycle_manager_safety', output='screen',
                parameters=[{
                    'use_sim_time': False,
                    'autostart': True,
                    'node_names': ['velocity_smoother', 'collision_monitor'],
                }],
            ),
        ]),

        # ── 4층: 최종 게이트 ────────────────────────────────────────────────
        # /cmd_vel 의 발행자는 이 노드 하나여야 한다. 노드가 스스로 발행자 수를
        # 세어 1 이 아니면 오류를 낸다 — 우회는 조용히 일어난다.
        Node(
            package='sentinel_safety',
            executable='safety_gate',
            name='safety_gate',
            output='screen',
            parameters=[{
                'input_topic': '/cmd_vel_safe',
                'output_topic': '/cmd_vel',
            }],
        ),
    ])

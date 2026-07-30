"""개발용 실시간 시각화 (S15P11A301-176).

`foxglove_bridge`를 띄워 노트북 Foxglove Studio가 젯슨 토픽을 실시간으로 보게
한다. 3D 패널에서 `/map`과 `/scan`을 켜면 SLAM 지도가 자라는 것이 보이고,
`/mission/status`를 Raw Messages에 걸면 상태 머신이 어디 있는지 보인다.

접속 (같은 네트워크):

    ws://jetson.sentinel-ugv.xyz:8765     Foxglove Studio 데스크톱 앱
                                          연결 유형은 "Foxglove WebSocket"이다.
                                          Rosbridge를 고르면 핸드셰이크가 깨진다.

## 이 도구가 필요한 이유

S15P11A301-173을 이걸로 찾았다. 라이다 설정이 ESP32를 가리켜 SLAM이 멎어
있었는데, 로그만 보면 "connected"가 찍혀 정상처럼 보였다. 지도가 자라지 않는
것을 눈으로 보고서야 알았다. 앞으로 탐사 주행(S15P11A301-172) 개발에서 Frontier
목표가 어디 찍히는지 확인하는 데도 같은 도구를 쓴다.

## 기본이 꺼져 있는 이유

데모 구성이 아니라 개발 도구다. WebSocket 서버가 8765를 열고 구독한 토픽을
직렬화하므로 CPU와 대역폭을 쓴다. Orin Nano에서 x264enc·YOLO가 이미 코어를 다
쓰고 있어(S15P11A301-131에서 오디오 손실로 겪었다) 데모 중에는 켜지 않는다.

## 평문 ws를 쓰는 이유

WHEP(S15P11A301-145)과 달리 TLS를 붙이지 않았다. 이 포트는 관제 웹 페이지가
아니라 개발자의 데스크톱 앱이 직접 연결하므로 혼합 콘텐츠 제약이 없고, LAN
안에서만 쓴다. 인증도 없으므로 **공개망에 노출하지 않는다** — 데모 때는 꺼져
있으니 문제가 되지 않는다.
"""

import os

from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, LogInfo
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

INSTALL_HINT = 'sudo apt install ros-humble-foxglove-bridge'


def generate_launch_description() -> LaunchDescription:
    """`foxglove_bridge`가 없으면 건너뛴다.

    이 패키지는 apt로 따로 설치하는 개발 도구이므로 없는 기기가 있다. 여기서
    예외를 올리면 **demo.launch 전체가 파싱에서 죽어** 스트리밍·녹화·관제까지
    올라오지 못한다. 시각화 하나 없다고 데모 스택을 잃으면 32장 장애 격리에
    어긋난다. demo.launch의 `_include` 가드는 우리 패키지의 파일 존재만 보므로
    이 확인은 여기서 해야 한다.
    """
    try:
        share = get_package_share_directory('foxglove_bridge')
    except PackageNotFoundError:
        return LaunchDescription([
            LogInfo(msg=(
                '[viz.launch] foxglove_bridge가 없어 시각화를 건너뛴다. '
                f'설치: {INSTALL_HINT}'
            )),
        ])
    bridge_launch = os.path.join(share, 'launch', 'foxglove_bridge_launch.xml')
    if not os.path.isfile(bridge_launch):
        return LaunchDescription([
            LogInfo(msg=(
                f'[viz.launch] {bridge_launch} 가 없어 시각화를 건너뛴다. '
                'foxglove_bridge 설치가 깨졌을 수 있다.'
            )),
        ])

    return LaunchDescription([
        DeclareLaunchArgument(
            'viz_port', default_value='8765',
            description='Foxglove WebSocket 포트.'),
        DeclareLaunchArgument(
            'viz_address', default_value='0.0.0.0',
            description='0.0.0.0이면 LAN의 노트북이 접속할 수 있다.'),
        IncludeLaunchDescription(
            # foxglove_bridge의 launch는 XML이다. PythonLaunchDescriptionSource로
            # 열면 파싱에서 죽는다.
            AnyLaunchDescriptionSource(bridge_launch),
            launch_arguments={
                'port': LaunchConfiguration('viz_port'),
                'address': LaunchConfiguration('viz_address'),
            }.items(),
        ),
    ])

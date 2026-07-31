"""데모 전체 스택을 한 줄로 올린다 (S15P11A301-156).

    ros2 launch sentinel_bringup demo.launch.py

재부팅 후 사람이 launch 대여섯 줄을 기억하는 대신 이것 하나(또는 systemd 유닛
`sentinel-demo`)로 데모 구성이 살아난다. 2026-07-29 밤 재부팅으로 스택 전체가
내려간 것을 다음날 아침에야 발견했다 — 데모 당일이었다면 검은 화면이었다.

## 단계별 지연

USB 장치 열림과 모델 로딩에는 시간이 걸린다. 전부 동시에 띄우면 부팅 직후
CPU·메모리 경합으로 NVMM 버퍼 할당이 실패하는 것을 실측했다(2026-07-30 아침,
가용 432MB 상태에서 nvbufsurface 오류). 그래서 무거운 것을 뒤로 민다.

     0s  sensors    usb_cam + lidar. 모든 것의 입력이다
     4s  slam       /scan 구독. 먼저 떠도 기다리기만 한다
     4s  streaming  카메라 토픽이 있어야 한다. 없어도 입력 감시가 기다린다
     8s  recorder   링 버퍼(index.json)를 읽는다. streaming보다 뒤
     8s  mission    토픽 구독뿐이라 순서 무관하지만 경합을 피해 늦춘다
    10s  bridge     MQTT 접속. 네트워크가 늦어도 자체 재시도가 있다
    12s  voice      encounter 대기. 모델은 실제 세션 시작 때 지연 로딩한다
    14s  detector   ai/detection wrapper. YOLO 모델 로딩이 가장 무겁다
    16s  viz        Foxglove 시각화. 기본 꺼짐(enable_viz). 토픽이 다 생긴 뒤

각 구성 요소는 `enable_*` 인자로 끌 수 있다. 개발 중 일부만 띄울 때 쓴다.

## 비밀번호

`~/.config/sentinel/secrets.yaml`(600, 커밋 금지)에서 읽는다.

    broker_password: <MQTT 비밀번호>

launch는 파라미터를 임시 파일로 노드에 넘기므로 `ps`에 노출되지 않는다.
파일이 없으면 bridge가 인증 실패를 로그로 남기고 나머지는 계속 돈다
(32장 장애 격리 — 관제 링크는 카메라·녹화와 독립이다).
"""

import os
from pathlib import Path

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    LogInfo,
    TimerAction,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

SECRETS_PATH = Path.home() / '.config' / 'sentinel' / 'secrets.yaml'


def _secret(key: str) -> str:
    """secrets 파일에서 값 하나를 읽는다. 없으면 빈 문자열.

    launch 생성 시점(파이썬)에 읽는다. 실패를 삼키는 이유는 비밀번호가 없다고
    카메라·녹화까지 못 뜨게 하면 안 되기 때문이다. bridge가 빈 값으로 접속을
    시도하고 거부 사유를 로그에 남기므로 원인은 보인다.
    """
    try:
        data = yaml.safe_load(SECRETS_PATH.read_text(encoding='utf-8'))
        return str(data.get(key) or '')
    except (OSError, yaml.YAMLError, AttributeError):
        return ''


def _include(package: str, launch_file: str, condition_arg: str,
             delay: float, launch_arguments=None):
    """launch 하나를 격리된 scope로 include한다.

    `GroupAction(scoped=True)`가 핵심이다. LaunchConfiguration은 launch context
    전역이라, lidar.launch가 설정한 `params_file`이 뒤에 include되는
    streaming·recorder·mission·bridge의 같은 이름 인자 기본값을 **조용히
    덮는다**(`DeclareLaunchArgument`의 default는 이미 설정된 값 앞에서
    무력하다). 실측에서 stream_pipeline이 `ydlidar_x4_pro.yaml`을 params로
    받았고, 각 노드가 코드 기본값으로 돌아 겉보기에는 정상이었다 — recorder의
    `no_response_timeout` 300초(S15P11A301-142 완화)가 조용히 30초로 퇴행한
    상태였다. scope를 나누면 각 include가 자기 기본값을 본다.
    """
    share = get_package_share_directory(package)
    launch_path = os.path.join(share, 'launch', launch_file)
    if not os.path.isfile(launch_path):
        # 죽는 대신 건너뛴다 (S15P11A301-160 진행 중 확인한 머지 순서 문제).
        # detection.launch.py는 S15P11A301-155가 넣는 파일이라, 이 브랜치가
        # 먼저 머지되면 파일이 아직 없다. 그때 launch 전체가 파싱에서 죽으면
        # 탐지 하나 없다고 스트리밍·녹화·관제까지 다 죽는다(32장 장애 격리
        # 위반). 건너뛴 사실은 로그로 남긴다 — 조용히 빠지면 "왜 탐지가
        # 없지"를 한참 찾는다.
        return LogInfo(msg=(
            f'[demo.launch] {package}/{launch_file} 가 없어 건너뛴다. '
            '해당 기능이 필요하면 그 파일을 넣는 브랜치를 먼저 머지한다.'
        ))
    include = GroupAction(
        scoped=True,
        condition=IfCondition(LaunchConfiguration(condition_arg)),
        actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(launch_path),
                launch_arguments=(launch_arguments or {}).items(),
            ),
        ],
    )
    if delay <= 0:
        return include
    return TimerAction(period=delay, actions=[include])


def generate_launch_description():
    broker_password = _secret('broker_password')

    return LaunchDescription([
        DeclareLaunchArgument('enable_sensors', default_value='true'),
        DeclareLaunchArgument('enable_slam', default_value='true'),
        DeclareLaunchArgument('enable_streaming', default_value='true'),
        DeclareLaunchArgument('enable_recorder', default_value='true'),
        DeclareLaunchArgument('enable_mission', default_value='true'),
        DeclareLaunchArgument('enable_bridge', default_value='true'),
        DeclareLaunchArgument('enable_voice', default_value='true'),
        DeclareLaunchArgument('enable_detector', default_value='true'),
        # 개발용 실시간 시각화 (S15P11A301-176). 기본 꺼짐 — 데모 구성이 아니라
        # 개발 도구다. WebSocket 서버가 토픽을 직렬화하느라 CPU를 쓰고, Orin
        # Nano에서 x264enc·YOLO가 이미 코어를 다 쓴다(S15P11A301-131에서 오디오
        # 손실로 겪었다). 필요할 때만 켠다:
        #   ./scripts/demo_up.sh enable_viz:=true
        DeclareLaunchArgument('enable_viz', default_value='false'),
        # 데모 기본은 TLS다. 관제 웹(HTTPS)이 평문 WHEP를 혼합 콘텐츠로
        # 차단한다(32-4, S15P11A301-145). 인증서가 없는 개발 기기에서만 끈다.
        DeclareLaunchArgument('webrtc_encryption', default_value='true'),

        _include('sentinel_bringup', 'sensors.launch.py',
                 'enable_sensors', 0),
        _include('sentinel_bringup', 'slam.launch.py',
                 'enable_slam', 4.0),
        _include('sentinel_streaming', 'streaming.launch.py',
                 'enable_streaming', 4.0,
                 {'webrtc_encryption': LaunchConfiguration('webrtc_encryption')}),
        _include('sentinel_recorder', 'recorder.launch.py',
                 'enable_recorder', 8.0),
        # 지도 저장 (S15P11A301-171). recorder와 같은 스위치를 쓴다 — 둘 다
        # "산출물을 로컬에 남긴다"이고 따로 끌 이유가 없다. SLAM이 없으면
        # 서비스가 없어 경고만 남기고 넘어간다.
        _include('sentinel_recorder', 'map_saver.launch.py',
                 'enable_recorder', 8.0),
        # media_uploader는 recorder.launch.py에 없어서 여기서 띄운다.
        # backend_base_url 기본값이 apex 도메인이라 API 호스트로 바로잡는다 —
        # 실물 검증(S15P11A301-140)에서 매번 손으로 넘기던 값이다.
        TimerAction(period=8.0, actions=[
            Node(
                package='sentinel_recorder',
                executable='media_uploader',
                name='media_uploader',
                output='screen',
                condition=IfCondition(LaunchConfiguration('enable_recorder')),
                parameters=[{
                    'backend_base_url': 'https://api.sentinel-ugv.xyz',
                }],
            ),
        ]),
        _include('sentinel_mission', 'mission.launch.py',
                 'enable_mission', 8.0),
        _include('sentinel_bridge', 'cloud_bridge.launch.py',
                 'enable_bridge', 10.0,
                 {'broker_password': broker_password}),
        _include('sentinel_bringup', 'voice.launch.py',
                 'enable_voice', 12.0),
        # ai/detection wrapper (S15P11A301-155, 도영훈의 실제 탐지).
        # 임시 통합용 person_detector(S15P11A301-136)를 쓰던 자리다 — 155가
        # sentinel_perception 패키지를 제거하고 wrapper launch로 대체했다.
        # S15P11A301-158이 이 wrapper로 전체 체인(탐지→임무→녹화)을 검증했다.
        _include('sentinel_bringup', 'detection.launch.py',
                 'enable_detector', 14.0),
        # 시각화는 가장 마지막이다. 다른 노드가 다 떠서 토픽이 존재해야 Foxglove
        # 첫 연결에 목록이 채워진다. 먼저 뜨면 빈 목록을 보고 새로 고쳐야 한다.
        _include('sentinel_bringup', 'viz.launch.py',
                 'enable_viz', 16.0),
    ])

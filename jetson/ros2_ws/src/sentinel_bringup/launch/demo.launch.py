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
from launch.substitutions import LaunchConfiguration, NotSubstitution
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
        # ESP32 두 보드 (S15P11A301-174, 데모 배선은 222). 이 패키지가 만들어진
        # 뒤에도 여기 include가 없어서 데모에서는 /wheel/odometry 와
        # /environment/* 가 **아예 발행되지 않았다.** cloud_bridge 가 그것을
        # 구독해 telemetry 를 채우게 만든 S15P11A301-213 이 그래서 값을 못 받고
        # 있었다 — 구독은 성공하고 값만 안 오므로 로그에도 남지 않는다.
        # **기본은 꺼짐이다.** 켜면 static identity 가 꺼지므로(아래 slam 참고)
        # 보드가 없을 때 odom TF 발행자가 0개가 되고 slam_toolbox 가 지도를 아예
        # 만들지 않는다. 지금 이 젯슨에 붙은 시리얼 장치는 라이다 하나뿐이다
        # (/dev/sentinel-lidar -> ttyUSB0, CP2102). 기본을 켜면 잘 도는 스택이
        # 깨진다.
        #
        # 보드를 물린 뒤에 이렇게 켠다:
        #   ./scripts/demo_up.sh enable_esp32:=true \
        #       motor_port:=/dev/ttyUSB1 sensor_port:=/dev/ttyUSB2
        #
        # 어느 포트가 모터/센서인지는 HELLO_ACK.board_role 로 확정한다
        # (esp32_bridge/TESTING.md). 역할이 어긋나면 핸드셰이크 로그에 오류가 난다.
        #
        # **켰는데 보드가 없으면 조용히 실패한다.** 노드는 죽지 않고 1초마다
        # 재시도하므로(SerialTransport 재시도) 프로세스는 살아 있고 토픽도
        # 광고되는데 데이터만 오지 않는다. 그 상태에서는 static identity 도 꺼져
        # 있어 odom TF 발행자가 0개가 되고, slam_toolbox 는 "Failed to compute odom
        # pose" 를 반복하며 지도를 만들지 않는다. 화면에서는 "지도가 안 나온다"로만
        # 보인다. 확인 방법은 둘이다:
        #
        #   ros2 run tf2_ros tf2_echo odom base_footprint   → 조회 실패
        #   demo 로그에 "not available ... retrying in 1.0s"
        DeclareLaunchArgument('enable_esp32', default_value='false'),
        # udev 별칭이 이 저장소에 없다. CP2102 클론 보드는 idVendor:idProduct:serial
        # 이 겹칠 수 있어 별칭으로 역할을 보장할 수 없고, HELLO_ACK.board_role 로
        # 확인하는 쪽을 신뢰한다는 것이 S15P11A301-174 의 판단이다. 빈 값이면
        # esp32_bridge.yaml 의 기본값(/dev/sentinel_mcu_*)을 쓴다.
        #   ./scripts/demo_up.sh motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1
        DeclareLaunchArgument('motor_port', default_value=''),
        DeclareLaunchArgument('sensor_port', default_value=''),
        DeclareLaunchArgument('enable_slam', default_value='true'),
        DeclareLaunchArgument('enable_streaming', default_value='true'),
        DeclareLaunchArgument('enable_recorder', default_value='true'),
        DeclareLaunchArgument('enable_mission', default_value='true'),
        DeclareLaunchArgument('enable_bridge', default_value='true'),
        DeclareLaunchArgument('enable_voice', default_value='true'),
        DeclareLaunchArgument('enable_detector', default_value='true'),
        # Foxglove Bridge (S15P11A301-176). **기본 켜짐으로 바꿨다
        # (S15P11A301-224)** — 관제 웹의 실시간 지도가 여기서 /map 을 받으므로
        # 더 이상 개발 도구가 아니라 제품 구성요소다.
        #
        # 끄면 관제 메인 화면의 지도가 비고, 그 이유가 화면에 안 나온다.
        #
        # 켜는 대가는 CPU 다. WebSocket 서버가 구독한 토픽을 직렬화하고 Orin
        # Nano 에서 x264enc·YOLO 가 이미 코어를 다 쓴다(S15P11A301-131 에서 오디오
        # 손실로 겪었다). 그래서 viz.launch.py 가 토픽을 여섯 개로 제한한다 —
        # 화이트리스트가 없으면 카메라 원본까지 직렬화한다.
        DeclareLaunchArgument('enable_viz', default_value='true'),
        # Nav2 는 기본 꺼짐 (S15P11A301-235). 통합 자원 계측(S15P11A301-249)이
        # 끝나기 전에는 데모 기본 구성을 건드리지 않는다 — 탐지가 이미 10.80FPS
        # 로 목표 미달이라, 얹었을 때 얼마나 깎이는지 수치 없이 켤 수 없다.
        DeclareLaunchArgument('enable_nav2', default_value='false'),
        # 데모 기본은 TLS다. 관제 웹(HTTPS)이 평문 WHEP를 혼합 콘텐츠로
        # 차단한다(32-4, S15P11A301-145). 인증서가 없는 개발 기기에서만 끈다.
        DeclareLaunchArgument('webrtc_encryption', default_value='true'),

        _include('sentinel_bringup', 'sensors.launch.py',
                 'enable_sensors', 0),
        # 센서와 함께 먼저 띄운다. slam_toolbox 가 odom→base_footprint TF 를
        # 요구하므로, 이쪽이 늦으면 SLAM 이 "Failed to compute odom pose" 를
        # 반복하며 그동안 지도를 만들지 않는다.
        _include('esp32_bridge', 'esp32_bridge.launch.py',
                 'enable_esp32', 0,
                 {
                     'motor_port': LaunchConfiguration('motor_port'),
                     'sensor_port': LaunchConfiguration('sensor_port'),
                     # 아래 slam 의 publish_static_odom 과 **정확히 반대**여야 한다.
                     'publish_odom_tf': LaunchConfiguration('enable_esp32'),
                 }),
        # odom→base_footprint 발행자를 하나로 유지한다 (S15P11A301-222).
        #
        # 이 TF 를 낼 수 있는 곳이 둘이다 — esp32_bridge 의 휠 오도메트리와
        # slam.launch.py 의 static identity. 둘 다 켜지면 같은 TF 를 다투어 위치가
        # 흔들리고, 둘 다 꺼지면 slam_toolbox 가 지도를 아예 만들지 않는다.
        # 어느 쪽이든 증상이 "지도가 이상하다"로만 보여 원인을 찾기 어렵다.
        #
        # 그래서 규약으로 두지 않고 구조로 묶는다. static 은 enable_esp32 의
        # 부정이므로 발행자가 항상 정확히 하나다. ESP32 가 없는 구성
        # (enable_esp32:=false)에서는 예전처럼 identity 로 돌아 SLAM 이 계속 된다.
        _include('sentinel_bringup', 'slam.launch.py',
                 'enable_slam', 4.0,
                 {
                     'publish_static_odom': NotSubstitution(
                         LaunchConfiguration('enable_esp32')
                     ),
                 }),
        # SLAM(4초) 뒤에 띄운다. global costmap 의 static layer 가 /map 을
        # 기다리는데, 먼저 뜨면 lifecycle activate 가 지도 없이 완료돼 빈
        # costmap 으로 시작한다 — latched 구독이라 회복은 되지만 로그가 어지럽다.
        _include('sentinel_bringup', 'nav2.launch.py',
                 'enable_nav2', 8.0),
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

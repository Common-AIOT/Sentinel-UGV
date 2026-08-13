"""스트리밍 경로 실행 (S15P11A301-106).

MediaMTX와 인코딩 노드를 함께 올린다. 카메라는 sensors.launch.py가 담당하므로
여기서 열지 않는다(명세 32-3 카메라 단일 오픈 원칙).

MediaMTX 실행 파일은 저장소에 커밋하지 않는다. scripts/setup_jetson.sh가
받아서 ~/.local/bin에 둔다. 다른 경로에 두었으면 mediamtx_binary 인자로 넘긴다.

발행 모드가 두 가지다.

    publish_mode:=rtsp        rtspclientsink로 publish (표준, gstreamer1.0-rtsp 필요)
    publish_mode:=udp_mpegts  mpegtsmux + udpsink, MediaMTX가 udp+mpegts로 수신

udp_mpegts를 지정하면 MediaMTX 경로 source를 환경변수로 덮어써서
mediamtx.yml을 고치지 않아도 되게 한다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import (
    LaunchConfiguration,
    PythonExpression,
)
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('sentinel_streaming')
    default_media_params = os.path.join(share, 'config', 'media.yaml')
    default_mediamtx_config = os.path.join(share, 'config', 'mediamtx.yml')
    default_mediamtx_binary = os.path.expanduser('~/.local/bin/mediamtx')

    # MediaMTX에 전용 작업 디렉터리를 준다. 상속받은 cwd에 파일을 쓰기 때문에,
    # 리포 안에서 launch하면 리포 안에 auto.crt / auto.key 같은 파일이 남는다.
    # 실제로 리포 최상위와 scripts/ 두 곳에 남은 것을 발견했다(S15P11A301-125).
    # 커밋되지는 않지만(gitignore가 *.crt/*.key를 제외한다) 실행 위치마다 쓰레기가
    # 쌓이고, 우리 인증서(CN=sentinel.local)와 헷갈린다.
    mediamtx_workdir = os.path.expanduser('~/.local/state/sentinel/mediamtx')
    os.makedirs(mediamtx_workdir, exist_ok=True)

    publish_mode = LaunchConfiguration('publish_mode')
    udp_host = LaunchConfiguration('udp_host')
    udp_port = LaunchConfiguration('udp_port')

    # MediaMTX 경로 source를 발행 모드에 맞춰 덮어쓴다.
    #
    # 빈 문자열을 주면 MediaMTX가 "invalid source: ''"로 거부하고 죽는다.
    # 환경변수가 설정되면 값이 반드시 유효해야 하므로 rtsp 모드에서는
    # mediamtx.yml과 같은 값(publisher)을 명시적으로 넣는다.
    mtx_source_override = PythonExpression([
        "'udp+mpegts://' + '", udp_host, "' + ':' + '", udp_port, "' "
        "if '", publish_mode, "' == 'udp_mpegts' else 'publisher'",
    ])

    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_media_params),
        DeclareLaunchArgument('mediamtx_config', default_value=default_mediamtx_config),
        DeclareLaunchArgument('mediamtx_binary', default_value=default_mediamtx_binary),
        DeclareLaunchArgument(
            'publish_mode', default_value='rtsp',
            description='rtsp 또는 udp_mpegts. rtspclientsink가 없으면 노드가 자동 폴백한다.'),
        DeclareLaunchArgument('udp_host', default_value='127.0.0.1'),
        DeclareLaunchArgument('udp_port', default_value='8890'),
        DeclareLaunchArgument(
            'launch_mediamtx', default_value='true',
            description='MediaMTX를 이 launch가 띄운다. 별도로 운영하면 false로 둔다.'),
        DeclareLaunchArgument(
            'webrtc_encryption', default_value='false',
            description='WHEP 엔드포인트를 HTTPS로 제공한다. 인증서가 있어야 한다.'),
        DeclareLaunchArgument(
            'webrtc_cert',
            default_value=os.path.expanduser('~/.config/sentinel/certs/server.crt'),
            description='scripts/gen_stream_cert.sh로 생성한다.'),
        DeclareLaunchArgument(
            'webrtc_key',
            default_value=os.path.expanduser('~/.config/sentinel/certs/server.key')),

        ExecuteProcess(
            cmd=[LaunchConfiguration('mediamtx_binary'),
                 LaunchConfiguration('mediamtx_config')],
            name='mediamtx',
            output='screen',
            cwd=mediamtx_workdir,
            condition=IfCondition(LaunchConfiguration('launch_mediamtx')),
            additional_env={
                'MTX_PATHS_SENTINEL_SOURCE': mtx_source_override,
                # HTTPS는 환경변수로 덮어쓴다. mediamtx.yml에는 인증서 경로를
                # 박지 않는다. 인증서는 커밋 대상이 아니고 배포마다 다르다.
                'MTX_WEBRTCENCRYPTION': LaunchConfiguration('webrtc_encryption'),
                'MTX_WEBRTCSERVERCERT': LaunchConfiguration('webrtc_cert'),
                'MTX_WEBRTCSERVERKEY': LaunchConfiguration('webrtc_key'),
            },
            # MediaMTX가 죽어도 인코딩 노드와 AI는 계속 동작해야 한다.
            # 따라서 이 프로세스 종료를 전체 launch 종료로 삼지 않는다(32장 장애 격리).
            respawn=True,
            respawn_delay=2.0,
        ),

        Node(
            package='sentinel_streaming',
            executable='stream_pipeline',
            name='stream_pipeline',
            output='screen',
            emulate_tty=True,
            parameters=[
                LaunchConfiguration('params_file'),
                {
                    'publish_mode': publish_mode,
                    'udp_host': udp_host,
                    'udp_port': ParameterAsInt(udp_port),
                },
            ],
        ),
    ])


def ParameterAsInt(substitution):
    """launch 인자는 문자열이므로 정수 파라미터로 넘기려면 변환이 필요하다."""
    return PythonExpression(['int(', substitution, ')'])

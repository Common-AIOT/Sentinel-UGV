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

## 이제 개발 도구가 아니다 (S15P11A301-224)

관제 웹의 실시간 지도가 이 bridge에서 `/map`을 받는다. 즉 **제품 구성요소**이고
데모 중에 켜져 있어야 한다. `demo.launch.py`의 `enable_viz` 기본값도 그래서
`true`다.

이 파일이 얼마 전까지 반대를 적고 있었다 — "데모 구성이 아니라 개발 도구다",
"인증도 없으므로 공개망에 노출하지 않는다, 데모 때는 꺼져 있으니 문제가 되지
않는다". 그 두 전제가 바뀌었으므로 아래 세 가지로 대응한다.

**TLS를 붙인다.** 관제 웹이 HTTPS이므로 평문 `ws://`는 브라우저가 혼합 콘텐츠로
차단한다. 인증서는 WHEP(S15P11A301-145)이 쓰는 것과 같은 파일이다 —
`jetson.sentinel-ugv.xyz` 이름으로 발급돼 있어 브라우저 경고가 없다.

**쓰기를 막는다.** 기본 capabilities에는 `clientPublish`·`services`·`parameters`가
들어 있어 접속만 하면 토픽을 발행하고 서비스를 부르고 파라미터를 바꿀 수 있었다.
`connectionGraph`만 남긴다. **이 변경으로 지금보다 안전해진다.**

**토픽을 제한한다.** 필요한 여섯 개만 광고한다. 노출면이 줄고 직렬화 비용도 줄어
CPU 경합(S15P11A301-131의 오디오 손실)을 완화한다.

남는 위험은 **읽기가 열린다**는 것이다. 젯슨이 공인 IP에 있어 인터넷에서 지도와
로봇 위치를 읽을 수 있다. 학생 프로젝트 범위에서 수용한 결정이며, 닫으려면
`viz_address:=127.0.0.1`로 띄우고 SSH 터널을 쓴다.
"""

import os
from pathlib import Path

from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
)
from launch.launch_description_sources import AnyLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

INSTALL_HINT = 'sudo apt install ros-humble-foxglove-bridge'

# WHEP(S15P11A301-145)이 쓰는 것과 같은 인증서다. 한 곳에서 갱신하면 둘 다 따라
# 간다 — 따로 두면 90일 뒤 한쪽만 만료돼 그쪽 증상만 나타난다.
_CERT_DIR = Path.home() / '.config' / 'sentinel' / 'certs'
DEFAULT_CERT = str(_CERT_DIR / 'server.crt')
DEFAULT_KEY = str(_CERT_DIR / 'server.key')


def _check_tls(context, *_args, **_kwargs):
    """TLS를 켰는데 인증서가 없으면 알린다.

    평문으로 떨어지지 않는다. 조용히 `ws://`로 뜨면 관제 웹(HTTPS)이 혼합
    콘텐츠로 막고, 화면에는 "지도 없음"만 보이며 그 이유가 로그에도 남지 않는다.
    Foxglove Studio 데스크톱 앱은 평문도 붙으므로 "Foxglove에서는 되는데 관제
    웹에서만 안 되는" 형태가 되어 원인을 찾기 특히 어렵다.

    죽이지는 않는다. 시각화 하나 때문에 데모 스택 전체를 잃으면 32장 장애 격리에
    어긋난다(이 파일 위쪽의 같은 판단). 대신 무엇을 해야 하는지 로그로 남긴다.
    """
    if LaunchConfiguration('viz_tls').perform(context).lower() not in (
        'true', '1', 'yes',
    ):
        return []

    missing = [
        path
        for path in (
            LaunchConfiguration('viz_certfile').perform(context),
            LaunchConfiguration('viz_keyfile').perform(context),
        )
        if not os.path.isfile(path)
    ]
    if not missing:
        return []
    return [LogInfo(msg=(
        '[viz.launch] TLS를 켰는데 인증서가 없다: ' + ', '.join(missing) + '. '
        'bridge가 평문 ws로 뜨면 관제 웹(HTTPS)이 혼합 콘텐츠로 막는다 — '
        '증상은 "지도 없음"으로만 보인다. 인증서를 배치하거나 '
        'viz_tls:=false 로 명시해 개발용으로 띄운다.'
    ))]


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
        # 관제 웹(HTTPS)이 붙으므로 기본이 TLS다. 끄면 브라우저가 혼합 콘텐츠로
        # 차단하며, 그때 화면에는 "지도 없음"만 보이고 이유가 어디에도 남지 않는다.
        # Foxglove Studio 데스크톱 앱만 쓸 때는 false로 둘 수 있다.
        DeclareLaunchArgument(
            'viz_tls', default_value='true',
            description='wss로 띄운다. 관제 웹이 붙으려면 반드시 true여야 한다.'),
        DeclareLaunchArgument(
            'viz_certfile', default_value=DEFAULT_CERT,
            description='WHEP(S15P11A301-145)과 같은 인증서를 쓴다.'),
        DeclareLaunchArgument(
            'viz_keyfile', default_value=DEFAULT_KEY,
            description='위 인증서의 키.'),
        # connectionGraph만 남긴다. 기본값에는 clientPublish·services·parameters가
        # 들어 있어 접속만 하면 로봇을 조작할 수 있었다.
        DeclareLaunchArgument(
            'viz_capabilities', default_value='[connectionGraph]',
            description='읽기 전용. 쓰기 capability를 넣지 않는다.'),
        # 정규식 리스트다(bridge 기본값이 ['.*']). 관제 웹의 지도·위치와 개발 중
        # 실제로 보던 것(스캔·TF)만 남긴다. 카메라 영상은 관제 웹에서 보므로 뺀다.
        DeclareLaunchArgument(
            'viz_topic_whitelist',
            default_value=(
                "['/map','/pose','/scan','/tf','/tf_static','/robot_description']"
            ),
            description='광고할 토픽 정규식 목록.'),
        # 인증서가 없으면 평문으로 떨어지지 않고 알린다. 조용히 ws로 뜨면 관제
        # 웹에서 혼합 콘텐츠로 막히고, 증상이 "지도가 안 나온다"로만 보인다.
        OpaqueFunction(function=_check_tls),
        IncludeLaunchDescription(
            # foxglove_bridge의 launch는 XML이다. PythonLaunchDescriptionSource로
            # 열면 파싱에서 죽는다.
            AnyLaunchDescriptionSource(bridge_launch),
            launch_arguments={
                'port': LaunchConfiguration('viz_port'),
                'address': LaunchConfiguration('viz_address'),
                'tls': LaunchConfiguration('viz_tls'),
                'certfile': LaunchConfiguration('viz_certfile'),
                'keyfile': LaunchConfiguration('viz_keyfile'),
                'capabilities': LaunchConfiguration('viz_capabilities'),
                'topic_whitelist': LaunchConfiguration('viz_topic_whitelist'),
            }.items(),
        ),
    ])

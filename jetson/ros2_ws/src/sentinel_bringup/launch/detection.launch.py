"""ai/detection 토픽 wrapper 실행 (S15P11A301-155, 명세 25.7).

usb_cam이 발행하는 `/camera/image_raw/compressed`를 구독해 사람을 탐지하고
확정 후보를 `/perception/person_candidates`(133 계약)로 발행한다. 카메라
장치를 직접 열지 않으므로 스트리밍 스택(sensors.launch.py)과 동시에 띄운다.

`ros2 run`을 쓸 수 없어 launch로 감싼다. `ultralytics`와 `torch`가 프로젝트
`.venv`에만 있고 ROS는 시스템 파이썬에 있다. 그래서 `ExecuteProcess`로 `.venv`
파이썬을 직접 부른다(구 sentinel_perception detector.launch.py와 같은 패턴).
`python -m src.ros_main`은 `ai/detection`을 작업 디렉터리로 요구하므로 `cwd`를
지정한다 — 설정·모델의 상대 경로도 그 기준이다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    LogInfo,
    RegisterEventHandler,
    TimerAction,
)
from launch.event_handlers import OnProcessExit
from launch.substitutions import LaunchConfiguration


def _repo_root() -> str:
    """저장소 루트를 찾는다.

    share 경로에서 고정된 단계 수를 되짚으면 `--symlink-install`에서 어긋난다.
    위로 올라가며 마커를 찾는다. `.venv`와 `ai/detection`이 함께 있는 곳이
    루트다. 실패하면 `SENTINEL_REPO_ROOT` 환경변수로 지정한다.
    """
    override = os.environ.get('SENTINEL_REPO_ROOT')
    if override:
        return override

    start = os.path.abspath(get_package_share_directory('sentinel_bringup'))
    current = start
    while True:
        if os.path.isdir(os.path.join(current, '.venv')) and os.path.isdir(
            os.path.join(current, 'ai', 'detection')
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            # 루트까지 올라갔는데 없다. 잘못된 경로로 조용히 도는 것보다
            # launch가 명확히 실패하는 편이 낫다.
            return start
        current = parent


def generate_launch_description() -> LaunchDescription:
    root = _repo_root()
    detection_dir = os.path.join(root, 'ai', 'detection')
    default_python = os.path.join(root, '.venv', 'bin', 'python')

    def make_process() -> ExecuteProcess:
        """탐지 프로세스 하나. 재기동마다 새로 만든다.

        launch 액션은 한 번만 실행되므로 같은 인스턴스를 다시 쓸 수 없다.
        """
        return ExecuteProcess(
            cmd=[
                LaunchConfiguration('python_executable'),
                '-m',
                'src.ros_main',
                '--config',
                LaunchConfiguration('config'),
                '--topic',
                LaunchConfiguration('camera_topic'),
                '--device',
                LaunchConfiguration('device'),
                '--output',
                LaunchConfiguration('output'),
            ],
            cwd=detection_dir,
            name='ai_detection_wrapper',
            output='screen',
        )

    # 재기동 횟수. dict로 두는 것은 클로저에서 갱신하기 위해서다.
    attempts = {'count': 0}

    def on_exit(event, context):
        """비정상 종료면 상한까지 다시 띄운다 (S15P11A301-192).

        `ExecuteProcess`는 `respawn`을 지원하지 않는다 — 그것은 launch_ros의
        `Node` 기능이다. 그래서 종료 이벤트를 받아 직접 다시 만든다. 덕분에
        32-3의 "무한 재시작 금지"를 상한으로 지킬 수 있다.

        탐지 노드가 죽으면 스택 나머지는 정상 기동하므로 화면상 정상으로
        보인다. 실제로 그 상태로 여러 검증을 돌린 뒤에야 알아챘다. 재기동과
        별개로 cloud_bridge가 `components.detector`를 관제에 보낸다.
        """
        if event.returncode == 0:
            return None

        limit = int(LaunchConfiguration('respawn_limit').perform(context))
        attempts['count'] += 1
        if attempts['count'] > limit:
            return [
                LogInfo(
                    msg=(
                        f'[detection] 탐지 노드가 {attempts["count"]}회 비정상 '
                        f'종료했다(exit {event.returncode}). 상한 {limit}회를 '
                        '넘겨 재기동을 멈춘다. 원인을 보려면 위 로그에서 '
                        'CUDA·메모리 오류를 확인한다. 관제에는 '
                        'components.detector=false로 나간다.'
                    )
                )
            ]

        delay = float(LaunchConfiguration('respawn_delay').perform(context))
        following = make_process()
        return [
            LogInfo(
                msg=(
                    f'[detection] 탐지 노드가 종료됐다(exit {event.returncode}). '
                    f'{delay:.0f}초 후 재기동 {attempts["count"]}/{limit}.'
                )
            ),
            TimerAction(period=delay, actions=[following]),
            RegisterEventHandler(
                OnProcessExit(target_action=following, on_exit=on_exit)
            ),
        ]

    first = make_process()

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'python_executable',
                default_value=default_python,
                description='torch와 ultralytics가 있는 파이썬. 기본은 프로젝트 .venv',
            ),
            DeclareLaunchArgument(
                'config',
                default_value='configs/pipeline.jetson.yaml',
                description='파이프라인 설정 (ai/detection 기준 상대 경로)',
            ),
            DeclareLaunchArgument(
                'camera_topic',
                default_value='/camera/image_raw/compressed',
                description='구독할 CompressedImage 토픽',
            ),
            DeclareLaunchArgument(
                'device',
                default_value='0',
                description='추론 장치 (예: 0, cpu)',
            ),
            DeclareLaunchArgument(
                'output',
                default_value='runs/ros2',
                description='events.jsonl 출력 디렉터리 (ai/detection 기준 상대 경로)',
            ),
            DeclareLaunchArgument(
                'respawn_limit',
                default_value='3',
                description='비정상 종료 시 재기동 횟수 상한 (0이면 재기동 없음)',
            ),
            DeclareLaunchArgument(
                'respawn_delay',
                default_value='8.0',
                description='재기동 대기 초. 자원 압박이 풀릴 시간을 준다',
            ),
            first,
            RegisterEventHandler(
                OnProcessExit(target_action=first, on_exit=on_exit)
            ),
        ]
    )

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
from launch.actions import DeclareLaunchArgument, ExecuteProcess
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
            ExecuteProcess(
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
            ),
        ]
    )

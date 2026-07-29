"""사람 탐지 노드 실행 (S15P11A301-136).

`ros2 run`을 쓸 수 없어 launch로 감싼다.

`ultralytics`와 `torch`가 프로젝트 `.venv`에만 있고 ROS는 시스템 파이썬에 있다.
colcon이 만드는 실행 스크립트의 shebang은 `/usr/bin/python3`로 박히므로
`ros2 run sentinel_perception ...`은 torch를 찾지 못한다.

그래서 `ExecuteProcess`로 `.venv` 파이썬을 직접 부른다. `.venv`가
`include-system-site-packages = false`인데도 `rclpy`가 보이는 이유는 ROS가
`PYTHONPATH`를 설정하고 venv가 그것을 무시하지 않기 때문이다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.substitutions import LaunchConfiguration


def _repo_root() -> str:
    """저장소 루트를 찾는다.

    share 경로에서 고정된 단계 수를 되짚으면 안 된다. `--symlink-install`이면
    share 안의 파일이 src를 가리키므로 계산이 어긋나고, 실제로 `ros2_ws`를
    루트로 잡아 `.venv`를 못 찾았다.

    위로 올라가며 마커를 찾는다. `.venv`와 `jetson/models`가 함께 있는 곳이 루트다.
    """
    override = os.environ.get('SENTINEL_REPO_ROOT')
    if override:
        return override

    start = os.path.abspath(get_package_share_directory('sentinel_perception'))
    current = start
    while True:
        if os.path.isdir(os.path.join(current, '.venv')) and os.path.isdir(
            os.path.join(current, 'jetson', 'models')
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            # 루트까지 올라갔는데 없다. 인자로 받은 값을 쓰게 하고 여기서는
            # 시작 지점을 돌려준다. 잘못된 경로로 실행되면 launch가 명확히
            # 실패하므로 조용히 잘못 도는 것보다 낫다.
            return start
        current = parent


def generate_launch_description() -> LaunchDescription:
    root = _repo_root()
    config = os.path.join(
        get_package_share_directory('sentinel_perception'), 'config', 'detector.yaml'
    )
    default_python = os.path.join(root, '.venv', 'bin', 'python')
    default_model = os.path.join(root, 'jetson', 'models', 'yolo26n.pt')

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                'python_executable',
                default_value=default_python,
                description='torch와 ultralytics가 있는 파이썬. 기본은 프로젝트 .venv',
            ),
            DeclareLaunchArgument(
                'model_path',
                default_value=default_model,
                description='YOLO 가중치 절대 경로',
            ),
            ExecuteProcess(
                cmd=[
                    LaunchConfiguration('python_executable'),
                    '-m',
                    'sentinel_perception.person_detector_node',
                    '--ros-args',
                    '--params-file',
                    config,
                    '-p',
                    ['model_path:=', LaunchConfiguration('model_path')],
                ],
                name='person_detector',
                output='screen',
            ),
        ]
    )

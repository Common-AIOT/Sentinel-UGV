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
    OpaqueFunction,
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


# 시스템 파이썬 패키지 경로. TensorRT 10.3.0 이 여기 있다.
#
# `.venv` 가 `include-system-site-packages = false` 라 그대로는 보이지 않는다
# (S15P11A301-102). venv 를 열어 해결하지 않는다 — 시스템 numpy 가 섞여 들어와
# 다른 문제를 만든다. PYTHONPATH 로만 더한다.
#
# **엔진을 쓸 때는 빌드와 추론 양쪽에 필요하다.** 실측에서 이것 없이 `--model` 만
# 넘기니 ultralytics 가 `import tensorrt` 에서 실패하고, pip 로 `tensorrt-cu12` 를
# 자동 설치하려다 그것도 실패한 뒤 노드가 exit 1 로 죽었다. 재기동 상한까지
# 반복하므로 **PyTorch 로 도는 것보다 나쁜 상태**가 된다.
SYSTEM_DIST_PACKAGES = '/usr/lib/python3.10/dist-packages'


def _engine_env() -> dict:
    """엔진 추론에 필요한 PYTHONPATH 를 만든다.

    기존 값을 지우지 않고 **뒤에 붙인다.** additional_env 는 그 키를 통째로
    대체하므로 지금 값을 먼저 읽어야 하고, ROS 가 거기에 자기 경로를 넣어 둔다.

    뒤에 붙이는 것이 중요하다. 앞에 두면 시스템 numpy 가 venv 것을 가려
    ultralytics·torch 가 다른 방식으로 깨진다. 뒤면 tensorrt(시스템에만 있다)는
    찾아지고 나머지는 venv 것이 이긴다.
    """
    current = os.environ.get('PYTHONPATH', '')
    parts = [p for p in current.split(os.pathsep) if p]
    if SYSTEM_DIST_PACKAGES not in parts:
        parts.append(SYSTEM_DIST_PACKAGES)
    return {'PYTHONPATH': os.pathsep.join(parts)}


def _resolve_model(context, detection_dir: str):
    """쓸 Detect 모델 경로를 정한다 (S15P11A301-225).

    `model` 인자가 비어 있으면 **아무것도 넘기지 않는다.** 설정 파일이 두 모델을
    모두 `.engine`으로 지정하고 `require_engine: true`로 강제하기 때문이다
    (S15P11A301-329). 여기서 할 일은 엔진 임포트 환경을 붙이고, 엔진이 없으면
    무엇을 해야 하는지 미리 알려주는 것뿐이다.

    반환값은 (cmd 에 붙일 인자 목록, 로그 액션 목록, 추가 환경변수)이다.

    ## 왜 이 함수가 있는가

    이 launch 가 `--model` 을 넘기지 않아 **데모가 계속 PyTorch 로 돌고 있었다**
    (S15P11A301-225). 설정 파일이 "엔진은 --model 인자로 넘긴다"고 적어 두었는데
    넘기는 곳이 없었다. S15P11A301-186 이 TensorRT 기준으로 측정한 detect_ms 51
    대신 PyTorch 의 74 로 돌던 것이다.

    그때는 여기서 엔진을 찾아 인자로 넘기는 방식으로 고쳤다. 329 에서 원인 자체를
    없앴다 — **설정이 엔진을 가리키고, 아니면 기동을 거부한다.** 넘길 인자가 없어야
    정상이며, 이 함수는 환경변수 담당으로 남는다.

    ## 엔진이 없으면 탐지 노드는 죽는다

    이전 판본은 여기서 "죽이지 않는다 — 엔진이 없는 기기에서도 데모는 돌아야
    한다(32장 장애 격리)"를 근거로 PyTorch 폴백을 허용했다. 329 는 그 폴백을
    없앴다. 조용히 43% 느려진 채 도는 것이 측정과 판단을 망가뜨린 실적이 있기
    때문이다(그 함정에 실제로 빠졌다).

    **장애 격리는 그대로 유지된다.** 죽는 것은 탐지 노드 하나이고 스택 28 개 노드는
    계속 돈다 — S15P11A301-172 가 `respawn_limit` 스코프를 고쳐 확보한 성질이다.
    재기동 3 회 뒤 탐지 없이 나머지가 돌며, 노드는 죽을 때 굽는 명령을 로그에
    남긴다(`model_backend.py`).
    """
    override = LaunchConfiguration('model').perform(context).strip()
    if override:
        # 사람이 명시했으면 존재 여부를 판단하지 않는다. 틀렸으면 노드가 그 이유를
        # 직접 말하는 편이 낫다.
        return (
            ['--model', override],
            [LogInfo(msg=f'[detection] 지정된 모델을 쓴다: {override}')],
            _engine_env() if override.endswith('.engine') else {},
        )

    missing = [
        rel for rel in (
            os.path.join('models', 'yolo26n.engine'),
            os.path.join('models', 'yolo26n-pose.engine'),
        )
        if not os.path.isfile(os.path.join(detection_dir, rel))
    ]
    if missing:
        return [], [LogInfo(msg=(
            f'[detection] TensorRT 엔진이 없다: {", ".join(missing)}. '
            'require_engine: true 이므로 탐지 노드가 기동을 거부한다 '
            '(스택의 나머지는 계속 돈다). 굽는 명령은 노드 로그와 '
            'configs/pipeline.jetson.yaml 주석에 있다.'
        ))], _engine_env()

    return [], [LogInfo(msg=(
        f'[detection] 설정의 TensorRT 엔진을 쓴다 '
        f'(PYTHONPATH 에 {SYSTEM_DIST_PACKAGES} 추가)'
    ))], _engine_env()


def generate_launch_description() -> LaunchDescription:
    root = _repo_root()
    detection_dir = os.path.join(root, 'ai', 'detection')
    default_python = os.path.join(root, '.venv', 'bin', 'python')

    # 모델 인자와 그 결정 로그. OpaqueFunction 안에서 한 번 정하고 재기동에도
    # 같은 값을 쓴다 — 엔진이 도중에 생기거나 사라지는 상황은 다루지 않는다.
    # 재기동 정책도 여기 담는다 (S15P11A301-249 후속, 2026-08-07).
    #
    # **이벤트 시점에 LaunchConfiguration 을 읽으면 안 된다.** demo.launch.py 는
    # `GroupAction(scoped=True)` 으로 include 하므로 이 launch 의 인자는 그 스코프
    # 안에만 있고, `OnProcessExit` 핸들러는 스코프가 닫힌 뒤에 돈다 — 그때
    # `LaunchConfiguration('respawn_limit').perform(context)` 가
    # 「launch configuration 'respawn_limit' does not exist」로 터지고 **launch
    # 전체가 내려간다.** 탐지 하나 죽었다고 스트리밍·녹화·관제까지 죽는 것이라
    # 32장 장애 격리를 정면으로 어긴다. 실측(2026-08-07): 탐지가 CUDA 메모리
    # 부족으로 죽자 스택 28개 노드가 전부 종료됐다.
    #
    # 그래서 setup(OpaqueFunction, 스코프 안)에서 한 번 읽어 둔다.
    resolved = {'model_args': [], 'env': {}, 'respawn_limit': 3, 'respawn_delay': 8.0}

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
                *resolved['model_args'],
            ],
            cwd=detection_dir,
            name='ai_detection_wrapper',
            output='screen',
            additional_env=resolved['env'],
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

        limit = resolved['respawn_limit']
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

        delay = resolved['respawn_delay']
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

    def setup(context, *_args, **_kwargs):
        """모델을 정한 뒤 프로세스와 이벤트 핸들러를 만든다.

        `OpaqueFunction` 이어야 한다. `model` 인자를 읽으려면 launch context 가
        필요하고, 파일 존재 확인 결과에 따라 cmd 를 다르게 만들어야 한다.
        """
        model_args, logs, env = _resolve_model(context, detection_dir)
        resolved['model_args'] = model_args
        resolved['env'] = env
        # 스코프가 살아 있는 지금 읽어 둔다. 위 주석 참고.
        resolved['respawn_limit'] = int(
            LaunchConfiguration('respawn_limit').perform(context)
        )
        resolved['respawn_delay'] = float(
            LaunchConfiguration('respawn_delay').perform(context)
        )
        first = make_process()
        return [
            *logs,
            first,
            RegisterEventHandler(
                OnProcessExit(target_action=first, on_exit=on_exit)
            ),
        ]

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
            DeclareLaunchArgument(
                'model',
                default_value='',
                description=(
                    'Detect 모델 경로 (ai/detection 기준 상대 경로). 비우면 '
                    '설정 파일이 가리키는 TensorRT 엔진을 쓴다. 엔진이 없으면 '
                    '탐지 노드가 기동을 거부한다 — PyTorch 폴백은 조용히 43% '
                    '느려진 채 돌아서 없앴다(S15P11A301-329). 스택의 나머지는 '
                    '계속 돌고, 굽는 명령은 로그에 남는다'
                ),
            ),
            OpaqueFunction(function=setup),
        ]
    )

"""저장소의 음성 가상환경으로 ROS 2 음성 세션 노드를 실행한다.

음성 모델 의존성은 ROS 시스템 Python이 아니라 저장소 ``.venv``에 설치되어 있다.
ROS 환경을 먼저 source한 뒤 이 launch를 실행하면 venv Python도 rclpy를 찾을 수
있다.
"""

import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, LogInfo, OpaqueFunction
from launch.substitutions import LaunchConfiguration


def _voice_process(context):
    repo_root = Path(LaunchConfiguration("repo_root").perform(context)).resolve()
    python = repo_root / ".venv" / "bin" / "python"
    stt_root = repo_root / "ai" / "stt"
    if not python.is_file():
        return [
            LogInfo(
                msg=(
                    f"[voice.launch] {python}가 없어 음성 노드를 건너뛴다. "
                    "ai/stt 의존성을 .venv에 설치한다."
                )
            )
        ]
    return [
        ExecuteProcess(
            cmd=[str(python), "-u", "-m", "sentinel_voice.ros_node"],
            cwd=str(stt_root),
            additional_env={
                "SENTINEL_DEVICE": "cpu",
                "PYTHONUNBUFFERED": "1",
            },
            output="screen",
        )
    ]


def generate_launch_description():
    default_root = os.environ.get(
        "SENTINEL_REPO_ROOT",
        str(Path.home() / "projects" / "S15P11A301"),
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("repo_root", default_value=default_root),
            OpaqueFunction(function=_voice_process),
        ]
    )

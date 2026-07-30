"""esp32_motor_bridge/esp32_sensor_bridge 두 노드를 함께 띄운다 (S15P11A301-84).

    ros2 launch esp32_bridge esp32_bridge.launch.py \\
        motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1

udev 별칭(/dev/sentinel_mcu_motor, /dev/sentinel_mcu_sensor)이 아직 없는
환경에서는 위처럼 실제 ttyUSB 경로를 직접 넘긴다(계획 §7, §8 참고).
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

OVERRIDABLE = ("motor_port", "sensor_port", "baudrate")


def _make_nodes(context):
    overrides = {}
    for name in OVERRIDABLE:
        value = LaunchConfiguration(name).perform(context)
        if value != "":
            overrides[name] = value
    if "baudrate" in overrides:
        overrides["baudrate"] = int(overrides["baudrate"])

    motor_params = [LaunchConfiguration("params_file")]
    sensor_params = [LaunchConfiguration("params_file")]
    if "motor_port" in overrides or "baudrate" in overrides:
        motor_override = {k: v for k, v in overrides.items() if k in ("motor_port", "baudrate")}
        if "motor_port" in motor_override:
            motor_override["port"] = motor_override.pop("motor_port")
        motor_params.append(motor_override)
    if "sensor_port" in overrides or "baudrate" in overrides:
        sensor_override = {k: v for k, v in overrides.items() if k in ("sensor_port", "baudrate")}
        if "sensor_port" in sensor_override:
            sensor_override["port"] = sensor_override.pop("sensor_port")
        sensor_params.append(sensor_override)

    return [
        Node(
            package="esp32_bridge",
            executable="esp32_motor_bridge",
            name="esp32_motor_bridge",
            output="screen",
            parameters=motor_params,
        ),
        Node(
            package="esp32_bridge",
            executable="esp32_sensor_bridge",
            name="esp32_sensor_bridge",
            output="screen",
            parameters=sensor_params,
        ),
    ]


def generate_launch_description():
    share = get_package_share_directory("esp32_bridge")
    default_params = os.path.join(share, "config", "esp32_bridge.yaml")

    return LaunchDescription(
        [
            DeclareLaunchArgument("params_file", default_value=default_params),
            *[DeclareLaunchArgument(name, default_value="") for name in OVERRIDABLE],
            OpaqueFunction(function=_make_nodes),
        ]
    )

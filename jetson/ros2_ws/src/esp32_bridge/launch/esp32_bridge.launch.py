"""esp32_motor_bridge/esp32_sensor_bridge 두 노드를 함께 띄운다 (S15P11A301-84).

    ros2 launch esp32_bridge esp32_bridge.launch.py \\
        motor_port:=/dev/ttyUSB0 sensor_port:=/dev/ttyUSB1

udev 별칭(/dev/sentinel_mcu_motor, /dev/sentinel_mcu_sensor)이 아직 없는
환경에서는 위처럼 실제 ttyUSB 경로를 직접 넘긴다(계획 §7, §8 참고).

`publish_odom_tf` 도 인자로 열어 두었다 (S15P11A301-222). 기본값은 yaml 의
false 이며 **단독 실행에서는 그대로 두어야 한다** — slam.launch.py 가
odom→base_footprint 를 static identity 로 발행하고 있으면 두 발행자가 같은 TF 를
다투어 위치가 흔들린다. 데모 스택에서는 demo.launch.py 가 두 값을 한 번에
뒤집어 발행자가 항상 정확히 하나가 되게 한다.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

OVERRIDABLE = ("motor_port", "sensor_port", "baudrate", "publish_odom_tf")


def _make_nodes(context):
    overrides = {}
    for name in OVERRIDABLE:
        value = LaunchConfiguration(name).perform(context)
        if value != "":
            overrides[name] = value
    if "baudrate" in overrides:
        overrides["baudrate"] = int(overrides["baudrate"])
    # 인자는 문자열로 오지만 파라미터는 bool 이어야 한다. 문자열 "false" 를 그대로
    # 넘기면 rclpy 가 형식 불일치로 거부하거나, 통과하더라도 비지 않은 문자열이라
    # 참으로 읽혀 **끄려고 준 인자가 켜는 결과**가 된다 (S15P11A301-222).
    if "publish_odom_tf" in overrides:
        overrides["publish_odom_tf"] = overrides["publish_odom_tf"].lower() in (
            "true", "1", "yes",
        )

    motor_params = [LaunchConfiguration("params_file")]
    sensor_params = [LaunchConfiguration("params_file")]
    if "motor_port" in overrides or "baudrate" in overrides:
        motor_override = {k: v for k, v in overrides.items() if k in ("motor_port", "baudrate")}
        if "motor_port" in motor_override:
            motor_override["port"] = motor_override.pop("motor_port")
        motor_params.append(motor_override)
    # publish_odom_tf 는 센서 보드 쪽 파라미터다. 오도메트리를 내는 것이 그쪽이다.
    sensor_keys = ("sensor_port", "baudrate", "publish_odom_tf")
    if any(key in overrides for key in sensor_keys):
        sensor_override = {k: v for k, v in overrides.items() if k in sensor_keys}
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

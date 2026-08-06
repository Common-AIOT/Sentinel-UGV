"""SLAM 보정과 EKF(IMU) yaw가 합쳐진 관제용 pose를 발행한다."""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        Node(
            package='sentinel_bridge',
            executable='fused_pose_publisher',
            name='fused_pose_publisher',
            output='screen',
            parameters=[{
                'map_frame': 'map',
                'odom_frame': 'odom',
                'base_frame': 'base_footprint',
                'map_topic': '/map',
                'slam_pose_topic': '/pose',
                'output_topic': '/pose/fused',
                'publish_rate_hz': 20.0,
            }],
        ),
    ])

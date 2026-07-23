from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    lidar_launch = PathJoinSubstitution([
        FindPackageShare('sentinel_bringup'), 'launch', 'lidar.launch.py'
    ])
    return LaunchDescription([
        Node(package='v4l2_camera', executable='v4l2_camera_node',
             namespace='camera', name='v4l2_camera', output='screen',
             parameters=[{'video_device': '/dev/video0',
                          'image_size': [1280, 720],
                          'time_per_frame': [1, 30],
                          'pixel_format': 'MJPG'}]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(lidar_launch))
    ])

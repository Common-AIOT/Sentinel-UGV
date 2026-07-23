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
    camera_params = PathJoinSubstitution([
        FindPackageShare('sentinel_bringup'), 'config', 'brio_100.yaml'
    ])
    return LaunchDescription([
        Node(package='usb_cam', executable='usb_cam_node_exe',
             namespace='camera', name='usb_cam', output='screen',
             parameters=[camera_params]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(lidar_launch))
    ])

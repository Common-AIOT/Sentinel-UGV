from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import LifecycleNode, Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    params = LaunchConfiguration('params_file')
    default_params = PathJoinSubstitution([
        FindPackageShare('sentinel_bringup'), 'config', 'ydlidar_x4_pro.yaml'
    ])
    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        LifecycleNode(package='ydlidar_ros2_driver', executable='ydlidar_ros2_driver_node',
                      name='ydlidar_ros2_driver_node', output='screen',
                      emulate_tty=True, parameters=[params]),
        Node(package='tf2_ros', executable='static_transform_publisher',
             name='static_tf_pub_laser',
             arguments=['0', '0', '0.02', '0', '0', '0', '1', 'base_link', 'laser_frame'])
    ])

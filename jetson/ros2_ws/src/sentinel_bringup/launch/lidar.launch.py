from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import LifecycleNode
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    params = LaunchConfiguration('params_file')
    default_params = PathJoinSubstitution([
        FindPackageShare('sentinel_bringup'), 'config', 'ydlidar_x4_pro.yaml'
    ])
    # TF 골격(base_footprint~camera_optical_frame)은 sentinel_description의
    # robot_state_publisher가 발행한다. sensors.launch.py는 이 파일을 include하므로
    # 단독/동시 실행 모두 여기서 한 번만 올린다.
    description_launch = PathJoinSubstitution([
        FindPackageShare('sentinel_description'), 'launch', 'description.launch.py'
    ])
    return LaunchDescription([
        DeclareLaunchArgument('params_file', default_value=default_params),
        LifecycleNode(package='ydlidar_ros2_driver', executable='ydlidar_ros2_driver_node',
                      namespace='', name='ydlidar_ros2_driver_node', output='screen',
                      emulate_tty=True, parameters=[params]),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(description_launch))
    ])

from setuptools import setup

package_name = 'sentinel_mission'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/mission.yaml']),
        ('share/' + package_name + '/launch', ['launch/mission.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='임무 상태 머신과 encounter 발행 단일 권한.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'mission_manager = sentinel_mission.mission_manager_node:main',
            'simulate_inputs = sentinel_mission.simulate_inputs:main',
        ],
    },
)

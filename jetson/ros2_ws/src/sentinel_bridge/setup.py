from setuptools import setup

package_name = 'sentinel_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/cloud_bridge.launch.py']),
        ('share/' + package_name + '/config', [
            'config/communication.yaml',
            # Mosquitto 참고 설정. EC2 적용은 S15P11A301-103이지만, 젯슨에서
            # 이 구성으로 TLS·인증·ACL을 검증했으므로 함께 둔다.
            'config/mosquitto.conf.example',
            'config/mosquitto-websocket.conf.example',
            'config/mosquitto-acl.example',
        ]),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='ROS 토픽을 MQTT로 변환해 관제 서버에 발행한다.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'cloud_bridge = sentinel_bridge.cloud_bridge_node:main',
        ],
    },
)

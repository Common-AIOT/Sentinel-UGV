from setuptools import setup

package_name = 'esp32_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name, package_name + '.tools'],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/esp32_bridge.launch.py']),
        ('share/' + package_name + '/config', ['config/esp32_bridge.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='모터·센서 ESP32와 USB 직렬(COBS+CRC16)로 통신하는 브리지 (S15P11A301-84)',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'esp32_motor_bridge = esp32_bridge.esp32_motor_bridge_node:main',
            'esp32_sensor_bridge = esp32_bridge.esp32_sensor_bridge_node:main',
            'esp32_hello_check = esp32_bridge.tools.hello_check:main',
        ],
    },
)

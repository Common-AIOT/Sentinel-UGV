from setuptools import setup

package_name = 'sentinel_safety'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='주행 안전 체인 — 명령 중재와 최종 게이트.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'command_mux = sentinel_safety.command_mux_node:main',
            'safety_gate = sentinel_safety.safety_gate_node:main',
        ],
    },
)

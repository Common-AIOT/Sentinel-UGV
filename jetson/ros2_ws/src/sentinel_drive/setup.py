from setuptools import setup

package_name = 'sentinel_drive'

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
    description='/cmd_vel 전륜 조향 역운동학(후륜 속도 + 조향각).',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'vehicle_kinematics = sentinel_drive.vehicle_kinematics_node:main',
        ],
    },
)

from setuptools import setup

package_name = 'sentinel_streaming'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/streaming.launch.py']),
        ('share/' + package_name + '/config',
         ['config/media.yaml', 'config/mediamtx.yml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='ROS 압축 토픽을 H.264로 인코딩해 MediaMTX에 발행한다.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'stream_pipeline = sentinel_streaming.stream_pipeline_node:main',
        ],
    },
)

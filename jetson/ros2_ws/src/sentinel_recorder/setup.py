from setuptools import setup

package_name = 'sentinel_recorder'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/recorder.launch.py']),
        ('share/' + package_name + '/config', ['config/recorder.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='링 버퍼 조각을 모아 이벤트 MP4를 만든다.',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'recording_manager = sentinel_recorder.recording_manager_node:main',
            'trigger_encounter = sentinel_recorder.trigger_encounter:main',
            'media_uploader = sentinel_recorder.media_uploader_node:main',
        ],
    },
)

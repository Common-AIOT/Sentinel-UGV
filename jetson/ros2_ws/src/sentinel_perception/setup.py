from setuptools import setup

package_name = 'sentinel_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
         ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/detector.yaml']),
        ('share/' + package_name + '/launch', ['launch/detector.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Sentinel Team',
    maintainer_email='team@example.com',
    description='YOLO 사람 탐지와 person_candidates 발행.',
    license='Apache-2.0',
    # console_scripts를 두지 않는다. 이 노드는 .venv 파이썬으로 실행해야 하고
    # colcon이 만드는 스크립트의 shebang은 시스템 파이썬으로 박힌다.
    # launch/detector.launch.py 를 쓴다. README의 「실행」 참고.
    entry_points={},
)

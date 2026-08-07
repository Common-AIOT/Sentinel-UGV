from setuptools import setup

package_name = 'sentinel_approach'

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
    description='PERSON_APPROACHING 접근 주행 (bearing-only).',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'approach = sentinel_approach.approach_node:main',
        ],
    },
)

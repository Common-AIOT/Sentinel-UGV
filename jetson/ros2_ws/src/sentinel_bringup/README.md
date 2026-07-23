# sentinel_bringup

전체 시스템 launch, lifecycle 순서와 장치별 파라미터 조합을 담당합니다. 시작 전 안전 상태를 확인하고 핵심 센서 실패 시 주행 노드를 활성화하지 않습니다.

## Jetson 센서 bringup

Logitech Brio 100과 YDLIDAR X4 Pro를 ROS 2 Humble에서 실행한다.

```bash
sudo apt install -y python3-vcstool v4l-utils \
  ros-humble-v4l2-camera ros-humble-image-transport-plugins
cd ~/projects/S15P11A301/jetson/ros2_ws
vcs import src < sentinel.repos
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

라이다 단독 실행:

```bash
ros2 launch sentinel_bringup lidar.launch.py
```

카메라·라이다 동시 실행:

```bash
ros2 launch sentinel_bringup sensors.launch.py
```

목표값은 카메라 `1280x720 @ 30 FPS`, `/scan` 약 `10~11 Hz`다. 상세 절차는 워크스페이스의 `CAMERA_LIDAR_INTEGRATION_TEST.md`를 참고한다.

# sentinel_bringup

전체 시스템 launch, lifecycle 순서와 장치별 파라미터 조합을 담당합니다. 시작 전 안전 상태를 확인하고 핵심 센서 실패 시 주행 노드를 활성화하지 않습니다.

## Jetson 센서 bringup

Logitech Brio 100과 YDLIDAR X4 Pro를 ROS 2 Humble에서 실행한다.

라이다 USB 권한이 없으면 사용자를 `dialout` 그룹에 추가한 뒤 로그아웃·로그인한다.

```bash
sudo usermod -aG dialout "$USER"
```

```bash
sudo apt install -y python3-vcstool v4l-utils \
  ros-humble-usb-cam ros-humble-image-transport-plugins
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

Brio 설정은 `config/brio_100.yaml`에 있으며 `usb_cam`의 `raw_mjpeg` 패스스루를 사용한다. 영상은 `/camera/image_raw/compressed`(JPEG)로만 발행되며, `usb_cam`은 raw_mjpeg 버그를 패치한 `src/usb_cam/`(0.8.1 벤더링) 빌드를 사용한다. Brio가 `/dev/video0`가 아니면 해당 파일의 `video_device`를 변경한다. `tri_test`는 라이다 ROS 2 드라이버와 동시에 실행하지 않는다.

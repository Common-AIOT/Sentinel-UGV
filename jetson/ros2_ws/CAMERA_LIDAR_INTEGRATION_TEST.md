# Logitech Brio 100 + YDLIDAR X4 Pro 통합 검증

환경: Jetson Orin Nano 8GB, JetPack 6.2.1+b38, ROS 2 Humble, Logitech Brio 100, YDLIDAR X4 Pro.

## 장치 확인

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
v4l2-ctl -d /dev/video0 --all
udevadm info --query=all --name=/dev/video0 | grep -E 'ID_SERIAL|ID_V4L_CAPABILITIES|ID_VENDOR'
```

`/dev/video0`은 실제 Brio 장치 경로로 바꾼다. `1280x720@30`에서 `MJPG` 또는 `YUYV` 지원 여부를 확인한다.

## 설치·빌드·실행

```bash
sudo apt update
sudo apt install -y python3-vcstool v4l-utils \
  ros-humble-image-transport-plugins
sudo usermod -aG dialout "$USER"
cd ~/projects/S15P11A301/jetson/ros2_ws
vcs import src < sentinel.repos
git -C src/usb_cam apply "$(pwd)/patches/usb_cam-0.8.1-raw-mjpeg-passthrough.patch"
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch sentinel_bringup sensors.launch.py
```

`dialout` 그룹 변경 뒤에는 로그아웃·로그인해야 한다. Brio가 `/dev/video0`가 아니거나 MJPG를 지원하지 않으면 `src/sentinel_bringup/config/brio_100.yaml`의 장치와 포맷을 변경한다. `tri_test`는 ROS 2 라이다 드라이버와 동시에 실행하지 않는다.

카메라는 `raw_mjpeg` 패스스루로 동작해 `/camera/image_raw/compressed`(`sensor_msgs/CompressedImage`, JPEG)만 발행한다. `/camera/image_raw`는 광고만 되고 데이터가 흐르지 않으므로 구독하지 않는다.

## 판정 및 기록

```bash
ros2 topic hz /scan
ros2 topic hz /camera/image_raw/compressed
ros2 topic bw /camera/image_raw/compressed
ros2 topic info /scan -v
ros2 topic info /camera/image_raw/compressed -v
tegrastats --interval 1000
watch -n 2 'date; lsusb; dmesg --ctime | tail -20'
```

RViz 2의 Fixed Frame은 `base_link`로 설정하고 `/scan`을 LaserScan, `/camera/image_raw/compressed`를 Image 디스플레이(Compressed)로 추가한다. 10분 이상 동시 실행하며 카메라 `1280x720 @ 30 FPS`, `/scan` 약 `10~11 Hz`, USB disconnect/reset, 영상 정지, 노드 종료 여부를 기록한다.

| 항목 | 기대값 | 측정값 | 판정/비고 |
|---|---:|---:|---|
| 해상도/FPS | 1280x720 / 30 Hz |  |  |
| 카메라 포맷 | MJPG 또는 YUYV |  |  |
| `/scan` | 10~11 Hz |  |  |
| 동시 실행 | 10분 이상 |  |  |
| CPU/RAM/GPU/USB | 이상 없음 |  |  |
| RViz 표시 | 영상·LaserScan |  |  |

## 제약사항

- YUYV는 MJPG보다 USB 대역폭 사용량이 커 프레임 드롭을 함께 점검한다.
- `usb_cam`은 apt 버전 대신 `sentinel.repos`로 받은 0.8.1 소스에 `patches/usb_cam-0.8.1-raw-mjpeg-passthrough.patch`(raw_mjpeg 패스스루 버그 수정)를 적용해 빌드한다.
- `/dev/video0`, `/dev/ttyUSB0`는 재연결 시 바뀔 수 있으므로 운영 환경에서는 udev 규칙으로 고정한다.

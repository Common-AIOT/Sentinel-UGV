# 카메라·라이다 통합 검증

환경: Jetson Orin Nano 8GB, JetPack 6.2.1+b38, ROS 2 Humble, Logitech Brio 100, YDLIDAR X4 Pro.

## 장치 및 실행

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
sudo usermod -aG dialout "$USER"
cd ~/projects/S15P11A301/jetson/ros2_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch sentinel_bringup sensors.launch.py
```

Brio가 `/dev/video0`가 아니거나 MJPG를 지원하지 않으면 `sensors.launch.py`의 장치와 포맷을 변경한다. `tri_test`는 ROS 2 라이다 드라이버와 동시에 실행하지 않는다.

## 판정 및 기록

```bash
ros2 topic hz /scan
ros2 topic hz /camera/image_raw
ros2 topic info /scan -v
ros2 topic info /camera/image_raw -v
tegrastats --interval 1000
```

10분 이상 동시 실행하며 카메라 `1280x720 @ 30 FPS`, `/scan` 약 `10~11 Hz`, RViz의 `/scan`·`/camera/image_raw` 동시 표시, USB disconnect/reset, 영상 정지, 노드 종료 여부를 기록한다.

| 항목 | 기대값 | 측정값 | 판정/비고 |
|---|---:|---:|---|
| 해상도/FPS | 1280x720 / 30 Hz |  |  |
| `/scan` | 10~11 Hz |  |  |
| 동시 실행 | 10분 이상 |  |  |
| CPU/RAM/GPU/USB | 이상 없음 |  |  |
| RViz 표시 | 영상·LaserScan |  |  |

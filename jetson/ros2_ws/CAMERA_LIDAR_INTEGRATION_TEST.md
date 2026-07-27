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
../../scripts/setup_jetson.sh
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
ros2 launch sentinel_bringup sensors.launch.py
```

`setup_jetson.sh`는 `usb_cam` raw_mjpeg 패치를 멱등하게 적용한다. 패치가 빠지면 압축 토픽이 재인코딩된 깨진 데이터가 되므로 손으로 `git apply`하지 않는다.

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

RViz 2의 Fixed Frame은 `base_footprint`로 설정하고 `/scan`을 LaserScan, `/camera/image_raw/compressed`를 Image 디스플레이(Compressed)로 추가한다. 10분 이상 동시 실행하며 카메라 `1280x720 @ 30 FPS`, `/scan` 약 `10~11 Hz`, USB disconnect/reset, 영상 정지, 노드 종료 여부를 기록한다.

TF 트리는 `sentinel_description`이 발행하므로 `base_footprint`를 Fixed Frame으로 쓸 수 있다(S15P11A301-74). `map`·`odom`은 아직 발행자가 없어 Fixed Frame으로 쓰면 실패한다.

### 측정 결과 (2026-07-27, 630초 연속)

| 항목 | 기대값 | 측정값 | 판정/비고 |
|---|---|---|---|
| 해상도/FPS | 1280x720 / 30 Hz | 29.927 Hz (최저 구간 29.893) | 합격. 주기 min 0.028s / max 0.040s, std dev 0.0020s |
| 카메라 포맷 | MJPG 또는 YUYV | MJPG (`raw_mjpeg` 패스스루) | 합격. 압축 토픽만 발행 |
| `/scan` | 10~11 Hz | 11.529 Hz (최저 구간 11.470) | 합격. 드라이버 목표 10 Hz 대비 상회 |
| 동시 실행 | 10분 이상 | 630초, 10초 간격 59회 샘플 | 합격. 전 샘플에서 노드 4개 생존 |
| CPU/RAM/GPU/USB | 이상 없음 | RAM 3038~3066MB(증가 추세 없음), tj 최대 46.5°C, `lsusb` 장치 수 2개 불변 | 합격. USB disconnect/reset 0회 |
| 영상 대역폭 | — | 1.01 MB/s, 프레임 평균 31.17KB (min 30.08 / max 32.62) | 참고값. 정적 실내 장면 기준 |
| RViz 표시 | 영상·LaserScan | 미검증 | GUI 상호작용이 필요해 이 측정에서는 확인하지 않았다. 토픽·TF는 CLI로 검증됨 |

노드 구성은 전 구간 `/camera/usb_cam`, `/robot_state_publisher`, `/ydlidar_ros2_driver_node` 3개와 launch 프로세스가 유지됐다. Fast DDS SHM 오류(`open_and_lock_file failed`)는 이 실행에서 **0건**이었다.

프레임 크기 31KB는 정적 실내 장면 값이고, S15P11A301-66 당시에는 약 105KB였다. 장면 복잡도에 따라 Fast DDS 단편화 임계(약 64KB) 양쪽을 오가므로 PoC-B에서 최대값을 별도로 측정한다([`jetson/streaming_poc/poc/README.md`](../streaming/poc/README.md)).

라이다 `Real points 43x > fixed points 430` 경고는 전 구간 반복됐으나 `/scan` 발행률과 메시지 유효성에는 영향이 없었다. `fixed_resolution: true` 설정에서 실제 포인트 수가 고정값을 넘을 때 나오는 경고다.

## 제약사항

- YUYV는 MJPG보다 USB 대역폭 사용량이 커 프레임 드롭을 함께 점검한다.
- `usb_cam`은 apt 버전 대신 `sentinel.repos`로 받은 0.8.1 소스에 `patches/usb_cam-0.8.1-raw-mjpeg-passthrough.patch`(raw_mjpeg 패스스루 버그 수정)를 적용해 빌드한다.
- `/dev/video0`, `/dev/ttyUSB0`는 재연결 시 바뀔 수 있으므로 운영 환경에서는 udev 규칙으로 고정한다.

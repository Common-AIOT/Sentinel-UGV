# ROS 2 workspace

ROS 2 Humble 워크스페이스입니다. 패키지 역할과 전체 기동 방법은
[Jetson README](../README.md), 장치별 검증은 각 패키지 README를 기준으로 합니다.

```bash
source /opt/ros/humble/setup.bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
colcon test
colcon test-result --verbose
```

전체 스택은 워크스페이스에서 launch를 직접 실행하기보다 저장소 루트의
`scripts/demo_up.sh`와 `scripts/demo_down.sh`를 사용합니다. 두 스크립트가 ROS 환경,
DDS 격리, 중복 기동 방지, ESP32 자동 감지를 일관되게 적용합니다.

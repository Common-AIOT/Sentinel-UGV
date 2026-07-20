# ROS 2 workspace

ROS 2 워크스페이스입니다. 각 패키지는 독립적으로 빌드·테스트할 수 있어야 하고, 센서가 없을 때 샘플 데이터 또는 시뮬레이션 입력을 지원합니다.

```bash
colcon build --symlink-install
colcon test
colcon test-result --verbose
```

# sentinel_description

Sentinel UGV의 URDF와 `base_footprint` 아래 정적 TF 골격을 담당합니다.

```bash
ros2 launch sentinel_description description.launch.py
```

## 현재 모델

`urdf/sentinel.urdf`는 2026-08-08 실측값을 반영한 11링크·10조인트 단일 트리입니다.
원점은 후륜 축 중앙의 바닥 투영이고 `base_footprint`와 `base_link`는 현재 같은
위치입니다.

| 링크 | `base_link` 기준 값·비고 |
|---|---|
| `imu_link` | `(0.233, -0.010, 0.215)m`, roll `-2.3°`, pitch `+9.3°` |
| `rear_drive_wheel_link` | `(0, 0, 0.125)m` |
| `front_left_wheel_link` | `(0.683, 0.268, 0.125)m` |
| `front_right_wheel_link` | `(0.683, -0.265, 0.125)m` |
| `ultrasonic_front_link` | `(0.810, 0, 0.215)m`, 전방 |
| `ultrasonic_rear_link` | `(-0.130, 0, 0.215)m`, yaw `π` |
| `lidar_link` | `(0.350, 0, 0.502)m`, yaw `0` 실측 확인 |
| `camera_link` | `(0.770, 0.010, 0.428)m`, pitch `-12.3°` |
| `camera_optical_frame` | REP-103 optical 변환 |

짐벌과 `head_imu_link`는 사용하지 않습니다. 앞바퀴 조인트도 실제 조향각 피드백이
없어 `fixed`입니다. 목표 조향각을 측정값처럼 TF로 발행하지 않습니다. 시각 형상은 아직
없으므로 Foxglove에서 URDF 모델 자체는 그려지지 않습니다.

## TF 소유권

이 패키지는 `base_footprint` 이하만 발행합니다.

- `map → odom`: SLAM Toolbox
- `odom → base_footprint`: 구성에 따라 정확히 하나만 선택
  - ESP32 없음: SLAM launch의 static identity
  - ESP32 있음, EKF 꺼짐: `esp32_sensor_bridge`
  - ESP32와 EKF 켜짐: `ekf_node`

`demo.launch.py`가 위 세 발행자의 배타를 구조적으로 보장합니다. 개발 편의용 static
publisher를 별도로 띄우면 같은 TF를 두 노드가 다투므로 사용하지 않습니다.

## 검증

```bash
python3 scripts/ci/validate_urdf_tree.py
check_urdf $(ros2 pkg prefix sentinel_description)/share/sentinel_description/urdf/sentinel.urdf
ros2 run tf2_tools view_frames
ros2 run tf2_ros tf2_echo base_footprint camera_optical_frame
ros2 run tf2_ros tf2_echo base_footprint lidar_link
ros2 run tf2_ros tf2_echo base_link imu_link
```

CI 검사는 XML 유효성만 보지 않고 루트 1개, 미선언 링크 없음, 자식 중복 없음, 순환
없음을 확인합니다. `robot_state_publisher`는 끊어진 트리도 기동할 수 있으므로 이 검사를
생략하면 특정 TF만 조용히 사라질 수 있습니다.

# sentinel_description

Sentinel UGV의 URDF 로봇 모델과 TF 정적 골격을 담당합니다. 명세 04장 8.3의 TF 트리를 `robot_state_publisher`로 발행합니다.

```bash
ros2 launch sentinel_description description.launch.py
```

- 모든 장착 오프셋은 placeholder이며 `urdf/sentinel.urdf`에서 갱신한다. 실측 파라미터 도입 시 xacro로 승격한다. 갱신 출처는 항목별로 다르다.

| 대상 | 갱신 출처 |
|---|---|
| 지상고, 후륜 축 위치 | TBD-HW-001 (RS540 구동계 실측) |
| 전륜 위치·조향 구조 | TBD-HW-008 (전륜 조향 구조) |
| LiDAR·카메라 장착 오프셋 | 명세 **35-7** 「LiDAR·카메라·차체 외부 파라미터」. `base_link` 원점 기준 X/Y/Z·RPY를 측정해 입력하고 `sensor_extrinsics.yaml`과 일치시킨다. 상류 의존성은 TBD-HW-006 |
| 차체 IMU 장착 오프셋 | 센서 ESP32에 연결하는 `imu_link` 1개만 사용한다. 모델·I2C/SPI 핀·X/Y/Z·RPY는 TBD-HW-012 |

- 짐벌 roll/pitch 조인트는 CENTER_LOCK fallback(각도 0)과 동일한 fixed로 시작했다. 짐벌 구현 시 revolute + joint_states 발행으로 전환한다.
- 짐벌용 `head_imu_link`는 사용하지 않으므로 S15P11A301-176에서 제거했다.
- `camera_link → camera_optical_frame`은 REP-103 optical 관례를 따르며 S15P11A301-62 카메라 계약의 `frame_id`와 일치한다.

## 발행 범위와 검증

이 패키지는 **`base_footprint` 이하만** 발행한다. `odom → base_footprint`는 오도메트리(EKF), `map → odom`은 SLAM이 발행하며 둘 다 아직 구현되지 않았다. 따라서 `map` 또는 `odom`을 기준으로 한 `tf2_echo`는 실패하는 것이 정상이다. 개발 편의로 임시 static publisher를 두지 않는다 — 실제 발행자와 경쟁해 조용히 깨진다.

```bash
check_urdf $(ros2 pkg prefix sentinel_description)/share/sentinel_description/urdf/sentinel.urdf
ros2 launch sentinel_description description.launch.py
```

> **조인트가 지워져도 XML 은 유효하다.** S15P11A301-344 의 커밋이 주석을 다시 쓰다
> 닫는 `-->` 로 `base_link_to_camera_link` 조인트를 삼켰고, 링크 선언은 남아 있어
> 파일이 정상으로 보였다. `robot_state_publisher` 는 끊어진 트리를 그대로 받아
> 기동하고 해당 TF 조회만 조용히 실패한다. 그래서 **루트가 하나인지**를 CI 에서
> 검사한다(S15P11A301-349) — 로봇 없이도 돈다:
>
> ```bash
> python3 scripts/ci/validate_urdf_tree.py
> ```
>
> 아래 `check_urdf` 출력의 `root = base_footprint` 가 그 불변식이다. 루트가 둘로
> 나오면 어떤 링크가 조인트 없이 떠 있다는 뜻이다.

별도 터미널에서 트리 검증:

```bash
ros2 run tf2_tools view_frames
ros2 run tf2_ros tf2_echo base_footprint camera_optical_frame
ros2 run tf2_ros tf2_echo base_footprint lidar_link
ros2 run tf2_ros tf2_echo base_link imu_link
```

`base_footprint → camera_optical_frame`은 quaternion `(-0.5, 0.5, -0.5, 0.5)`, `base_footprint → lidar_link`는 translation `(0, 0, 0.02)`이 나와야 한다. 후자는 기존 `base_link → laser_frame` flat static TF의 net 변환을 유지한 값이다.

2026-07-27 실측 검증(Jetson Orin Nano, ROS 2 Humble):

```text
check_urdf                            : Successfully Parsed XML,
                                        root = base_footprint, 링크 13개
/tf_static child frames               : 12개 (루트 제외 전부 발행)
base_footprint -> camera_optical_frame: quat (-0.500, 0.500, -0.500, 0.500)
base_footprint -> lidar_link          : xyz (0, 0, 0.020), 회전 identity
lidar.launch.py 노드                  : robot_state_publisher +
                                        ydlidar_ros2_driver_node
static_tf_pub_laser                   : 제거 확인
/scan frame_id                        : lidar_link
/scan 발행률                          : 약 11.55 Hz
```

위 링크 수는 2026-07-27 당시 짐벌용 `head_imu_link`를 포함한 기록이다.
S15P11A301-176에서 해당 링크를 제거한 현재 모델은 링크 12개, 정적 child frame
11개가 정상이다.

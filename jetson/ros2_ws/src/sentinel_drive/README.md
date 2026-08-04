# sentinel_drive

`/cmd_vel` 차동 구동 역운동학 (S15P11A301-234). 자율주행 사슬의 마지막 ROS 노드다.

```text
/cmd_vel (Twist)  →  vehicle_kinematics  →  /esp32_motor_bridge/drive_command (JSON String)
                      역운동학·곡률 보존 포화·watchdog
```

## 부호 계약

근거는 `esp32_bridge/wheel_odometry.py` 의 정운동학 `delta_yaw = (right − left) / W` 다.
역은 `left = v − ωW/2, right = v + ωW/2` — **반시계(ω>0)는 오른쪽이 빠르다.**
시험 `test_정운동학과_왕복이_항등이다` 가 이 왕복을 못박는다.

## 파라미터 (잠정값)

| 파라미터 | 기본 | 확정 |
|---|---|---|
| `track_width_m` | 0.30 | TBD-CAL-002 실측 (S15P11A301-248). 틀리면 회전 반경만 어긋난다 |
| `max_wheel_mmps` | 300 | RS540 실측 전 보수적 상한 (24.2 수동 상한과 동일) |
| `cmd_vel_timeout_s` | 0.3 | ESP32 watchdog(300ms)과 별개의 ROS 측 1차 방어선 |
| `mode` | 2 (AUTO) | 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2 |

## 시험

```bash
python3 -m pytest jetson/ros2_ws/src/sentinel_drive/test -q
```

순수 파이썬(numpy 불필요)이라 CI 의 `test:message-contract` 잡에서 함께 돈다.

# sentinel_drive

`/cmd_vel` 전륜 조향 역운동학 (S15P11A301-234·297). 자율주행 사슬의 마지막 ROS 노드다.

```text
/cmd_vel (Twist)  →  vehicle_kinematics  →  /esp32_motor_bridge/drive_command (JSON String)
                      자전거 모델 변환·포화·거부·watchdog     후륜 좌·우 mm/s + 조향각 mdeg
```

## 2026-08-06 하드웨어 변경: 차동 구동 → 전륜 조향

앞쪽 캐스터 2개를 떼고 전륜 조향부를 복구했다. 전진·후진은 후륜 RS540 2개,
조향은 전륜 타이로드에 직결된 DS51150 서보 1개다(02장 6.3).

```text
v_cmd = clamp(v, ±v_max)
δ     = clamp(atan(L·ω / v_cmd), ±δ_max)
후륜 좌 = 후륜 우 = v_cmd        # 좌·우 속도 차로 회두를 만들지 않는다
R_min = L / tan(δ_max)
```

**제자리 회전은 할 수 없다.** `abs(v) < v_min` 이고 `ω ≠ 0` 인 명령은 거부하고
(구동 0, 조향각 유지) `/diagnostics` 에 카운터를 올린다(§34-2). 정공법은 Nav2 쪽에서
rotation shim·spin 복구를 끄는 것이고(24.1) 이 거부는 마지막 방어선이다.

## 부호 계약

`δ > 0` 이 좌회전(반시계)이고 `v > 0` 에서 `ω = v·tanδ/L > 0` 이다 — REP-103 과
프로토콜 `target_steering_mdeg`("+= 좌회전")가 같은 규약이다. 후진은 같은 식이 그대로
성립한다(`ω > 0` 을 후진으로 내려면 `δ < 0`). 시험 `test_정운동학과_왕복이_항등이다` 와
`test_후진_반시계는_조향이_뒤집힌다` 가 이것을 못박는다.

## 정지해도 조향은 중립으로 돌리지 않는다

watchdog 정지·종료 정지·제자리 회전 거부 모두 **마지막 조향각을 실어 보낸다**(§34-7).
정지가 곧 정차가 아니라 관성으로 더 가기 때문이며, 그때 중립으로 꺾으면 피하려던
장애물 쪽으로 밀려 나갈 수 있다. 중립으로 가는 예외는 ESP32 부팅 직후 하나뿐이다.

## 파라미터 (잠정값)

| 파라미터 | 기본 | 확정 |
|---|---|---|
| `wheelbase_m` | 0.50 | TBD-HW-008 실측. 틀리면 직진은 맞고 선회 반경만 어긋난다 |
| `max_steering_deg` | 30.0 | TBD-HW-008 실측(§35-3). **`steering.cpp` 의 `STEERING_MAX_MDEG`(30000)와 같아야 한다** |
| `max_drive_mmps` | 300 | RS540 실측 전 보수적 상한 (24.2 수동 상한과 동일) |
| `min_linear_mmps` | 30 | v_min. `steering.cpp` 의 `STEERING_MIN_DRIVE_MMPS` 와 같은 값 |
| `max_steering_rate_mdps` | 60000 | §35-4 「조향 튜닝」 실측. 0 을 보내면 서보가 자기 최대 속도로 꺾는다 |
| `cmd_vel_timeout_s` | 0.3 | ESP32 watchdog(300ms)과 별개의 ROS 측 1차 방어선 |
| `mode` | 2 (AUTO) | 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2 |

펌웨어와 값을 맞춰야 하는 파라미터가 둘(`max_steering_deg`, `min_linear_mmps`)이다.
어긋나면 Jetson 이 보낸 명령을 펌웨어가 조용히 클램프·거부하고 `/diagnostics` 에
`STEERING_COMMAND_INVALID` 만 올라온다.

## 시험

```bash
python3 -m pytest jetson/ros2_ws/src/sentinel_drive/test -q
```

순수 파이썬(numpy 불필요)이라 CI 의 `test:message-contract` 잡에서 함께 돈다.

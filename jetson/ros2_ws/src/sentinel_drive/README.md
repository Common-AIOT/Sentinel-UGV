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

## 파라미터

| 파라미터 | 기본 | 확정 |
|---|---|---|
| `wheelbase_m` | 0.683 | 2026-08-06 실측. 전·후 차축 중심 거리 |
| `max_steering_deg` | 22.0 | 앞바퀴 실제 조향각 실측. 서보 회전각이 아님 |
| `max_drive_mmps` | 300 | RS540 실측 전 보수적 상한 (24.2 수동 상한과 동일) |
| `min_linear_mmps` | 30 | v_min. `steering.cpp` 의 `STEERING_MIN_DRIVE_MMPS` 와 같은 값 |
| `max_steering_rate_mdps` | 60000 | §35-4 「조향 튜닝」 실측. 0 을 보내면 서보가 자기 최대 속도로 꺾는다 |
| `cmd_vel_timeout_s` | 0.3 | ESP32 watchdog(300ms)과 별개의 ROS 측 1차 방어선 |
| `mode` | 2 (AUTO) | 03-204: SAFE_IDLE=0, MANUAL=1, AUTO=2 |

`max_steering_deg`와 펌웨어 `STEERING_MAX_MDEG`는 모두 앞바퀴 각 상한이므로 현재
22°로 맞춰져 있다. 펌웨어는 `SERVO_MAX_OFFSET_DEG=55`와의 비로 바퀴 22°를 서보
55°에 매핑한다. 코드 반영은 끝났지만 외부 조향각 센서가 없는 개루프이므로
`scripts/steering_measure.py`로 실제 회전반경이 `R_min≈1.69m`인지 인수해야 한다.
참고로 이 패키지 단위 시험의 로컬 30° fixture는 아직 현재 기본값과 동기화되지 않았다.

## 시험

```bash
python3 -m pytest jetson/ros2_ws/src/sentinel_drive/test -q
```

순수 파이썬(numpy 불필요)이라 CI 의 `test:message-contract` 잡에서 함께 돈다.

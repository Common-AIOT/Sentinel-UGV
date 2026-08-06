# sentinel_exploration

Frontier·관측 목표 선택과 카메라 커버리지 장부 (S15P11A301-172, 명세 23.3~23.4·설계 v2는 티켓 코멘트).

## 구조

판단은 전부 rclpy 없는 순수 모듈에 있고, ROS 껍데기는 노드 하나다.

```text
frontier.py    자유/미지 경계 추출·군집화·필터(크기·테두리·반경)·소멸 판정
coverage.py    카메라 커버리지 장부(world 키·레이캐스트) + 관측 후보(소스 B)
selector.py    점수화(m²·m 정규화) + 약속(commitment) 정책 — 진동 방지
sweep.py       스텝-드웰 관측 스윕 계획(부채꼴 수학)
blacklist.py   도달 실패한 자리를 임무 동안 기억(좌표 반경)
navigator.py   NullNavigator / Nav2Navigator(NavigateToPose). rclpy 쪽
exploration_node.py   구독·발행·시계·TF 만. Navigator 인터페이스 뒤에 주행
```

## 왜 카메라 커버리지인가

라이다는 360° 라 방 중앙을 한 번 지나가면 지도가 완성되는데, 사람을 찾는 것은
전방 약 52° 카메라다. frontier 만 좇으면 **지도는 완벽한데 구석에 쓰러진 사람은
화각에 한 번도 들어오지 않는다.** 그래서 목표 소스가 둘이다 — frontier(지도)와
관측 후보(카메라 미관측 free 군집). 종료도 2단이다: frontier 소진 후 미관측
면적이 임계 미만이어야 완료다.

## 주행 (S15P11A301-172)

`Nav2Navigator` 가 `NavigateToPose` 로 목표를 보낸다. **노드 기본값은 여전히
`null`** 이고, `exploration.launch.py` 가 `navigator:=nav2` 를 준다 — 이 노드가 다른
경로로 켜졌을 때 예상 밖에 모터가 도는 일을 막는다.

세 가지가 함께 있어야 바퀴가 돈다.

```bash
./scripts/demo_up.sh enable_nav2:=true enable_exploration:=true enable_safety:=true
```

`enable_exploration` 은 `enable_nav2` 와 **AND 로 묶여 있다** — Nav2 없이 탐사만 켜면
목표를 보낼 곳이 없어 「탐사가 도는데 제자리」가 된다. `enable_safety` 는 자동으로
켜지지 않는다. 그것이 **실제로 모터를 돌리는 스위치**이므로 사람이 따로 켠다
(S15P11A301-298).

### 결말 처리 — 안 움직이는 것을 막는 부분

| 결말 | 처리 |
|---|---|
| `SUCCEEDED` | 그 자리의 실패 이력을 지운다 |
| `FAILED`(거부 포함) | 실패 적립. 3회면 임무 동안 후보에서 제외 |
| `UNAVAILABLE` | 액션 서버 없음. **실패로 세지 않는다** |
| `CANCELED` | 우리가 취소한 것(게이트 닫힘·사람 발견) |

**약속(commitment)을 결말에서 푼다.** 풀지 않으면 도달한 자리에 약속이 남아, 새
후보가 125% 를 넘길 때까지 로봇이 서 있는다.

**실패를 기억하지 않으면 영원히 안 움직인다.** 점수는 지도에서 나오므로 거부당한
후보가 다음 주기에 또 1위이고, 2초마다 같은 목표로 다시 보낸다. `allow_unknown:
false` 와 겹칠 때 특히 잘 난다 — frontier 는 정의상 미지 공간의 경계다.

**`UNAVAILABLE` 을 실패와 나눈 것이 요점이다.** Nav2 는 lifecycle 묶음이라 활성까지
몇 초 걸린다. 그동안을 실패로 세면 블랙리스트가 정상 후보를 3회 만에 먹어치우고
탐사가 시작도 못 하고 `DONE` 이 된다.

### 안 움직일 때 보는 순서

`~/status` 의 `state` 가 어디서 멈췄는지 말해 준다.

| state | 뜻 |
|---|---|
| `HOLD` | `movementAllowed=false`. 임무가 EXPLORING 이 아니거나 상태가 낡았다 |
| `WAIT_MAP` | `/map` 을 못 받았다. slam_toolbox 와 QoS(latched) 확인 |
| `DONE` | 후보가 없다. `blockedGoals` 가 크면 「다 봤다」가 아니라 「못 갔다」다 |
| `DRIVING` | 목표를 보냈다. 여기서 바퀴가 안 돌면 아래(Nav2·안전 체인·운동학)다 |

## 시험

```bash
python3 -m pytest jetson/ros2_ws/src/sentinel_exploration/test -q
```

픽스처 `test/fixtures/map_351x372.npz` 는 돌고 있는 slam_toolbox 에서 캡처한
**실제 지도**다(351×372, 0.05m/셀, origin (-9.34, -10.00)). 갱신하려면
foxglove_bridge 에서 `/map` CDR 을 받아 `np.savez_compressed` 로 저장한다.

CI 는 `test:exploration` 잡(python:3.10-slim — numpy 휠 때문에 alpine 이 아니다)
이 돌린다.

## 파라미터 (잠정값 — 확정 주체는 티켓 설계 v2 표)

| 파라미터 | 기본 | 뜻 |
|---|---|---|
| `camera_hfov_deg` | 52 | BRIO 100 대각 58° 의 수평 환산. 캘리브레이션 실측 전 |
| `detect_range_m` | 5.0 | 탐지를 신뢰하는 거리. 누운 사람 기준 실측 전 |
| `max_radius_m` | 12 | home 기준 탐사 반경 상한 (시연장 경계) |
| `coverage_done_m2` | 1.5 | 2단 종료 임계 — 미관측 면적이 이보다 작으면 완료 |
| `sweep_sectors` | 9 | 360°/40°. HFOV 대비 겹침 확보 |
| `max_angular_for_coverage_radps` | 0.2 | 이보다 빨리 돌면 커버리지로 안 친다 (블러) |
| `breadcrumb_spacing_m` | 0.5 | 복귀 2단(23.6) 입력 기록 간격 |

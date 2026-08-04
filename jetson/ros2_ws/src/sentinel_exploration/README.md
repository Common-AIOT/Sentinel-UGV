# sentinel_exploration

Frontier·관측 목표 선택과 카메라 커버리지 장부 (S15P11A301-172, 명세 23.3~23.4·설계 v2는 티켓 코멘트).

## 구조

판단은 전부 rclpy 없는 순수 모듈에 있고, ROS 껍데기는 노드 하나다.

```text
frontier.py    자유/미지 경계 추출·군집화·필터(크기·테두리·반경)·소멸 판정
coverage.py    카메라 커버리지 장부(world 키·레이캐스트) + 관측 후보(소스 B)
selector.py    점수화(m²·m 정규화) + 약속(commitment) 정책 — 진동 방지
sweep.py       스텝-드웰 관측 스윕 계획(부채꼴 수학)
exploration_node.py   구독·발행·시계·TF 만. Navigator 인터페이스 뒤에 주행
```

## 왜 카메라 커버리지인가

라이다는 360° 라 방 중앙을 한 번 지나가면 지도가 완성되는데, 사람을 찾는 것은
전방 약 52° 카메라다. frontier 만 좇으면 **지도는 완벽한데 구석에 쓰러진 사람은
화각에 한 번도 들어오지 않는다.** 그래서 목표 소스가 둘이다 — frontier(지도)와
관측 후보(카메라 미관측 free 군집). 종료도 2단이다: frontier 소진 후 미관측
면적이 임계 미만이어야 완료다.

## 지금 로봇은 움직이지 않는다

Navigator 기본이 `NullNavigator` 다 — 목표를 `~/goal` 로 발행하고 로그만 남긴다.

- `/cmd_vel` → 좌·우 바퀴 속도 역운동학이 없다 (S15P11A301-234)
- Nav2 스택이 구성되지 않았다 (S15P11A301-235)

둘이 오면 `Nav2Navigator` 를 같은 인터페이스에 꽂는다. 이 패키지는 바뀌지
않는다. 축소판(순찰 시퀀스)도 같은 자리에서 갈아끼운다.

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

# sentinel_mission

임무 상태 머신과 `/perception/encounter` 발행 단일 권한입니다 (S15P11A301-133, 명세 26장).

## 이 패키지가 있는 이유

26.1이 정한 단일 권한 원칙입니다. `/perception/encounter`를 발행하는 노드는
`mission_manager_node` 하나입니다.

이 토픽에 여러 노드가 발행하면 세 가지가 깨집니다.

`encounterId`를 누가 발급할지 정해지지 않으므로 탐지·음성·주행이 서로 다른 UUID를
씁니다. 녹화 노드는 `encounterId`로 같은 이벤트인지 판단하므로(32-6) 한 사람에
대한 이벤트가 둘·셋으로 쪼개집니다.

`CONFIRMED` 없이 `ENDED`가 먼저 도착할 수 있습니다. 녹화 상태 머신은 진행 중
이벤트가 없는 신호를 무시하므로 대화 구간이 통째로 빠진 영상이 나옵니다.
S15P11A301-123 검증에서 실제로 그렇게 됐고, 그때는 발행 타이밍 문제였지만
발행자가 여럿이면 상시 상태가 됩니다.

`LOST`와 `REDETECTED`는 사후 3초 창 안에서만 의미가 있습니다. 서로 모르는 노드가
발행하면 그 창을 지킬 수 없습니다.

## 26.2 임무 상태와 32-5 녹화 phase는 같은 것입니다

별 상태 머신이 아닙니다. 26.3의 전이가 일어날 때 그 부산물로 phase를 냅니다.

```text
26.3 전이                                    발행할 phase
EXPLORING → PERSON_APPROACHING               CONFIRMED
PERSON_APPROACHING → INTERACTING             APPROACHED
INTERACTING → POST_RECORDING                 ENDED
POST_RECORDING → INTERACTING (재감지)         REDETECTED
진행 중 사람 상실                              LOST
```

두 상태 머신을 두면 서로 어긋날 때 어느 쪽이 맞는지 알 수 없습니다.

`sentinel_recorder`의 `RecordingStateMachine`과도 역할이 다릅니다. 그쪽은 녹화
자원(조각 수집, MP4 생성, 디스크 상한)을 관리하고 이쪽은 임무 상태만 판단합니다.
녹화 쪽 상태를 이쪽이 알 필요가 없고, 알면 녹화 실패가 주행을 멈추게 됩니다.

## 입출력

```text
입력  /perception/person_candidates   탐지 노드가 확정한 사람 후보 (25.2)   BEST_EFFORT
      /mission/signal                 주행·음성·관제·안전이 알리는 사건 (26.1)  RELIABLE

출력  /perception/encounter           녹화 트리거 (32-5). 발행자는 이 노드뿐  RELIABLE
      /mission/status                 임무 상태 (26.2)                      RELIABLE
```

계약은 `common/schemas/`의 `person-candidates`, `mission-signal`,
`mission-status`, `encounter` 네 스키마입니다. `test/test_mission_state.py`가 코드의
enum과 스키마의 enum이 갈라지지 않는지 검사합니다. 한쪽만 고치면 다른 팀원이 맞출
대상이 실제 코드와 달라집니다.

### QoS를 이렇게 고른 이유

후보는 프레임마다 오고 한 프레임을 놓쳐도 다음이 옵니다. 탐지 노드가
`BEST_EFFORT`로 발행할 가능성이 높으므로 이쪽도 `BEST_EFFORT`입니다. **RELIABLE
구독자는 BEST_EFFORT 발행자와 매칭되지 않아 한 건도 받지 못합니다.**
S15P11A301-123에서 `/scan`으로 겪었습니다.

encounter는 잃으면 이벤트가 사라지므로 `RELIABLE`입니다. 녹화 노드가 기본
QoS(RELIABLE, depth 10)로 구독하므로 맞아야 합니다.

## 보내는 쪽은 사실만 적습니다

`/mission/signal`에 "무슨 일이 있었는지"만 적고 "어떤 상태로 가야 하는지"는 적지
않습니다. 주행 노드는 정지한 것을 알고 음성 노드는 대화가 끝난 것을 알지만, 그것이
`INTERACTING`으로 가야 한다는 판단은 이 노드의 몫입니다. 보내는 쪽이 목표 상태를
지정하면 26.1의 단일 권한이 무의미해집니다.

## 음성 파이프라인과의 연결

`ai/voice`는 순수 파이썬이고 ROS를 모릅니다. `report_lifecycle.execute_outcome`이
`request_mission_resume: Callable[[], bool]` 콜백을 받는데, 그 구현체가 없는 것이
현재 상태입니다.

그 콜백은 `/mission/signal`에 `RESUME_REQUESTED`를 발행하면 됩니다. 음성 쪽을 ROS
노드로 만들 필요는 없고, 얇은 어댑터 노드가 라이브러리를 호출하며 콜백만 ROS
발행으로 채우면 됩니다. 그 어댑터는 S15P11A301-117의 몫입니다.

`RESUME_REQUESTED`는 `REPORTING`에서만 통합니다. `PAUSED`에서는 무시합니다. 30.5가
"안전 장애·미디어 저장 실패·Mission Manager 오류가 있으면 자동 재개하지 않고
`PAUSED`를 유지한다"고 정했으므로, 운영자의 명시적 `RESUME_APPROVED`만 `PAUSED`를
풀 수 있습니다.

## 탐지 노드와의 분담

25.2의 확정 규칙 중 프레임 단위 판단은 탐지 노드가 합니다. 프레임 데이터를 갖고
있는 쪽이 그것입니다.

```text
class가 person이다                          탐지 노드
confidence가 설정값 이상이다                 탐지 노드
동일 track이 약 1초 동안 최소 관측 횟수 만족   탐지 노드
박스 크기·위치가 비정상적으로 급변하지 않는다   탐지 노드
카메라 timestamp와 TF를 조회할 수 있다        탐지 노드
이미 활성 encounter에 포함된 track인지        mission_manager_node
```

마지막 항목만 다릅니다. 활성 encounter를 아는 것은 이 노드뿐이므로 그쪽이
판단합니다(25.4 중복 제거).

**후보가 없으면 빈 배열을 보내야 합니다.** 발행을 멈추면 안 됩니다. 발행이 끊긴
것을 사람이 사라진 것으로 해석하면, 탐지 노드가 죽었을 때 진행 중 이벤트가 조용히
종료됩니다.

## MVP 범위

26.2의 12개 상태 중 encounter 경로와 안전 전이를 구현합니다.

```text
구현    SAFE_IDLE, EXPLORING, PERSON_APPROACHING, INTERACTING,
        POST_RECORDING, REPORTING, PAUSED, MANUAL, COMPLETED, ESTOP, ERROR
자리만  RETURNING
```

`RETURNING`은 home pose와 Nav2 목표 전송이 필요합니다(23.5). 범위 밖이므로 상태 값만
두고 전이 트리거를 받지 않으며, 그 상태로 들어가면 경고를 남깁니다.

`MANUAL`은 S15P11A301-298에서 구현했습니다. 종전 근거였던 「control session과 gamepad
deadman」(36장)은 성립하지 않습니다 — 그 UI는 삭제됐고, 조종은 폰이 모터 ESP32에
직결하는 경로로 확정됐습니다.

그래서 이 머신은 모드를 **판단하지 않고 따라갑니다.** 들어오는 신호는
`MANUAL_ENGAGED`/`AUTO_ENGAGED`, 즉 **보드가 이미 그렇게 됐다는 사실**입니다. 운영자
의도(`MANUAL_REQUESTED`/`AUTO_REQUESTED`)는 `mode_gateway`가 상태기계 앞에서 가로채
보드에 물어보고, 답이 온 뒤에야 사실로 바꿔 넣습니다.

- 진입은 26.3·14.2대로 `PAUSED`를 경유합니다. 탐사 중이었다면 한 틱 안에
  `EXPLORING → PAUSED → MANUAL` 두 전이가 순서대로 발행됩니다(`_pending_manual`).
- 이탈은 관제 「자율」 하나뿐이고 착지점은 `PAUSED`입니다. 자동 재개는 어떤 경로로도
  없습니다(SR-008, 30.5).
- `MOVEMENT[MANUAL] = (False, None)`은 자리표시자가 아니라 정확한 값입니다. deadman은
  폰에 있고 젯슨에는 수동 속도 소스 자체가 없습니다.

`SENSOR_FAULT`는 `PAUSED`로 갑니다. 26.5는 "`PAUSED` 또는 `ERROR`"라고만 정했고
어느 쪽인지는 14.5가 정합니다. 복구 가능한 것을 `ERROR`로 만들면 운영자가 재개할
방법이 없습니다.

## 실행

```bash
cd jetson/ros2_ws && source install/setup.bash
ros2 run sentinel_mission mission_manager --ros-args \
  --params-file install/sentinel_mission/share/sentinel_mission/config/mission.yaml
```

`auto_start`는 기본 false입니다. 26.4가 "재시작 후 진행 중이던 임무를 자동 주행으로
복구하지 않는다"고 정했습니다. 개발 중 편의로 켤 수 있지만 운영에서는 끕니다.

## AI 없이 검증하기

탐지 노드(#99~#102)와 주행·음성이 아직 없으므로 입력을 모사합니다.

```bash
ros2 run sentinel_mission simulate_inputs --scenario normal
ros2 run sentinel_mission simulate_inputs --scenario group            # 32-6
ros2 run sentinel_mission simulate_inputs --scenario out-of-order     # 26.1의 존재 이유
ros2 run sentinel_mission simulate_inputs --scenario lost
ros2 run sentinel_mission simulate_inputs --scenario redetect
ros2 run sentinel_mission simulate_inputs --scenario approach-failed  # 30.3
ros2 run sentinel_mission simulate_inputs --scenario estop            # 26.5
```

`sentinel_recorder`의 `trigger_encounter`와 역할이 다릅니다. 그쪽은 녹화 노드만
단독으로 시험하기 위해 `/perception/encounter`를 직접 발행합니다. 이쪽은 그 상위
경로, 즉 사실 입력에서 encounter가 만들어지는 과정을 검증합니다. 둘 다 남깁니다.

발행자를 만든 직후에 발행하면 DDS 매칭이 끝나지 않아 메시지가 사라집니다.
`simulate_inputs`는 `get_subscription_count()`로 구독자를 기다립니다.
S15P11A301-123에서 `CONFIRMED`가 사라지고 `ENDED`만 도착해 녹화가 조용히 아무것도
하지 않았습니다.

### 발행자가 하나인지 확인

이 티켓의 핵심 완료 조건입니다.

```bash
ros2 topic info /perception/encounter --verbose
```

`Publisher count`가 1이어야 합니다. 2 이상이면 26.1이 깨진 것입니다.

## 검증 기록 (2026-07-28)

`stream_pipeline` + `recording_manager` + `mission_manager`를 함께 띄우고 입력만
모사한 end-to-end입니다.

```text
발행자 수     /perception/encounter publisher 1, subscription 1
normal       SAFE_IDLE → EXPLORING → PERSON_APPROACHING → INTERACTING
             → POST_RECORDING → REPORTING → EXPLORING
             phase CONFIRMED, APPROACHED, ENDED 순서대로, encounterId 하나
group        사람 1 → 3명, encounterId 하나, personCount 3
             MP4 1개 3627KB 11.867초 356프레임, 사전 3.59초
out-of-order 확정 전에 온 SAFE_POSE_REACHED·DIALOGUE_ENDED·REPORT_COMMITTED
             셋 다 사유와 함께 무시, 이후 정상 경로 완주
lost         PERSON_APPROACHING → POST_RECORDING (person lost) → REPORTING
estop        REPORTING → ESTOP, 이후 RESUME_APPROVED 무시(latch)
단위시험      30건 (상태 전이·그룹·안전·시간·계약)
```

### 이 검증에서 잡은 결함

**사람이 늘어도 `CONFIRMED`를 다시 내지 않았습니다.** `group` 시나리오에서
`personCount`가 1→3으로 늘었는데 녹화 보고서에는 1명으로 남았습니다. 3명을
발견했는데 기록에 1명이면 32-6이 요구한 "동시에 발견된 사람들"이 사라집니다.

이제 활성 encounter에 새 track이 들어오면 `CONFIRMED`를 다시 냅니다. 같은
`encounterId`의 반복 `CONFIRMED`는 안전합니다. 녹화 노드가 그것으로 이벤트를
쪼개지 않습니다(S15P11A301-123의
`test_repeated_confirmed_does_not_split_event`). `REPORTING`에서는 내지 않습니다.
사후 3초가 끝나 녹화 노드가 마감하는 중이므로 새 `CONFIRMED`가 이벤트를
되살립니다.

이것이 발행 주체를 하나로 모아야 하는 이유의 실례입니다. 탐지 노드가 직접
발행했다면 "사람이 늘었으니 다시 알린다"를 판단할 근거가 없습니다. 활성 encounter를
모르기 때문입니다.

## 문제 해결

### 후보를 보내는데 encounter가 안 만들어진다

`/mission/status`의 `state`를 봅니다. `SAFE_IDLE`이나 `PAUSED`면 새 encounter를
만들지 않습니다. 26.2가 이동을 허용하지 않는 상태이고, 접근 없이 encounter를 만들면
녹화만 돌다 타임아웃으로 끝납니다.

`MISSION_START`를 보내 `EXPLORING`으로 가야 합니다.

### 신호를 보냈는데 아무 일도 안 일어난다

노드 로그를 봅니다. 무시한 신호는 반드시 사유를 남깁니다.

```text
SAFE_POSE_REACHED(NAVIGATION) 무시: SAFE_POSE_REACHED는 PERSON_APPROACHING에서만 유효하다(현재 EXPLORING)
```

사유가 없으면 메시지 자체가 도착하지 않은 것입니다. QoS 불일치를 먼저 확인합니다.

### 후보가 계속 오는데 사람을 놓쳤다고 판정한다

`observedAt`이 실제 관측 시각인지 확인합니다. 이 노드는 그 값으로 상실을
판정하므로, 발행 시각과 크게 다르면 유예 시간이 이미 지난 것으로 봅니다.

`usb_cam`의 `header.stamp`는 노드 시작 시점에 계산한 고정 오프셋을 쓰므로 진짜 wall
clock이 아닙니다. 탐지 노드가 그 값을 그대로 `observedAt`에 쓰면 안 됩니다.

### 링 writer와 녹화 노드를 함께 띄웠는데 조각이 안 생긴다

`stream_pipeline`을 두 개 띄우지 않았는지 확인합니다. 하드웨어 MJPEG 디코더를 두
프로세스가 동시에 쓰면 한쪽이 죽습니다.

```text
NVMMLITE_NVVIDEODEC video_parser_parse Unsupported Codec
ERROR:gstsplitmuxsink.c:2691:check_completed_gop: assertion failed: (gop != NULL)
```

`scripts/demo_down.sh`로 전부 내린 뒤 필요한 것만 띄웁니다. MediaMTX가 없으면
`rtspclientsink`가 재연결 루프에 빠져 조각이 나오지 않는 것도 같은 증상입니다.

이전에는 이 자리에 부분 기동 스크립트(`stop_sentinel.sh`)가 적혀 있었는데
**그것으로는 전부 내려가지 않았습니다**(S15P11A301-217). 센서·스트리밍만
정리하므로 SLAM·녹화·임무·탐지가 남고, 그 상태로 다시 띄우면 같은 토픽을 중복
발행하는 고아가 쌓입니다. 그 스크립트는 S15P11A301-294에서 제거했습니다 —
내리는 명령이 둘이면 한쪽은 반드시 낡습니다.

`sentinel-demo.service`가 돌고 있으면 `demo_down.sh`가 `systemctl stop`을 탑니다.
프로세스만 죽이면 `Restart=on-failure`가 5초 뒤 스택을 되살립니다.

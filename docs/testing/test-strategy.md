# Test Strategy

> 기준: 통합 프로젝트 명세서 v0.9, 2·14·16·20장
> 상태: 테스트 단계와 안전 gate **확정**, 장치별 수치 기준은 실측 후 확정

## 목표

기능 완료는 코드 작성이나 Merge만을 의미하지 않습니다. 재현 가능한 명령, 기대 결과, 실제 결과와 실패 시 안전 동작을 증명해야 합니다. 실제 하드웨어가 없어도 샘플, replay, simulation으로 대부분의 실패를 먼저 검출합니다.

## 검증 계층

| 계층 | 실행 시점 | 대상 | 대표 도구·증적 |
|---|---|---|---|
| 정적 검사 | 모든 MR | 포맷, lint, 설정, 계약 | ShellCheck, formatter, schema validation |
| 단위 테스트 | 모든 MR | 계산·상태 전이·변환 | JUnit, Vitest/Jest, pytest |
| 컴포넌트 | 모듈 MR | API, ROS node, uploader | Testcontainers, launch test, mock server |
| replay/simulation | 통합 전 | 녹화 영상, rosbag, 가짜 telemetry | fixture, rosbag replay |
| 통합 | develop | Jetson bridge↔Backend↔Frontend | Compose, WebSocket, DB 조회 |
| 벤치 | 하드웨어 변경 | 모터, E-Stop, watchdog, 전원 | 체크리스트, 로그, 영상 |
| 장애 주입 | 시나리오 준비 | Wi-Fi, 센서, S3, gamepad 단절 | 타임스탬프 로그, 복구 결과 |
| 성능·리허설 | release 후보 | FPS, 지연, 온도, 3회 시나리오 | CSV, 그래프, 시연 영상 |

## Merge Request gate

모든 MR은 다음을 충족해야 합니다.

- 관련 Jira 이슈와 변경 이유
- 재현 가능한 검증 명령
- 자동 테스트 또는 자동화가 불가능한 이유
- 하드웨어·안전 영향
- rollback 방법
- 성공한 필수 CI
- 최소 1명의 reviewer

모터·E-Stop·전원·TTL 변경에는 임베디드 reviewer와 벤치 증적이 추가로 필요합니다.

## 모듈별 최소 자동 검사

| 모듈 | 초기 필수 검사 | 애플리케이션 생성 후 추가 |
|---|---|---|
| 공통 | JSON/YAML 구문, schema와 sample 일치 | 호환성·consumer contract |
| Backend | repository structure | Gradle test, static analysis, Flyway validate, Docker build |
| Frontend | repository structure | lint, typecheck, unit/component test, production build |
| Jetson Python | Shell/config 검사 | lint, unit test, launch/config validation |
| ROS 2 | package 구조 | colcon build/test, rosbag replay |
| 배포 | Local/EC2 Compose render | Nginx/MediaMTX config, health smoke test |

## 안전 테스트 gate

하드웨어 테스트 전:

- 차량을 바닥에서 띄웁니다.
- 물리 E-Stop 담당자를 지정합니다.
- 모터 방향과 좌·우 채널을 저속으로 확인합니다.
- 배선, 퓨즈, 전압, 커넥터 고정을 확인합니다.
- 테스트 종료 조건과 최대 시간을 합의합니다.

필수 시험:

| ID | 시험 | 합격 기준 |
|---|---|---|
| SAFE-01 | 프로그램 시작 | 초기 모터 출력 0 |
| SAFE-02 | 정상 종료·SIGINT | 종료 직후 0, 서비스 재시작 전 비활성 |
| SAFE-03 | 예외 주입 | `finally`/watchdog으로 정지 |
| SAFE-04 | 명령 TTL 만료 | 목표 500ms 이내 정지 |
| SAFE-05 | gamepad 단절 | MANUAL 종료, 0, PAUSED |
| SAFE-06 | 물리 E-Stop | 소프트웨어와 무관하게 모터 전원 차단 |
| SAFE-07 | LiDAR stale | 자율 명령 중단과 오류 이벤트 |
| SAFE-08 | 모터 무응답 | ESTOP 및 재명령 금지 |

## 기능 시험 기준

### 자율주행

- `NAV-01`: 수동 주행 지도에서 복도 벽이 연속적으로 보입니다.
- `NAV-02`: 정적 목표에 도달하거나 안전하게 실패합니다.
- `NAV-03`: 정적 장애물을 충돌 없이 우회하거나 정지합니다.
- `NAV-05`: 사용자 목표 없이 Frontier를 선택합니다.
- `NAV-06`: Frontier 소진 후 재스캔하고 종료·복귀합니다.
- `NAV-07`: home pose 허용 오차 내 복귀합니다. 허용 오차는 실측 후 고정합니다.

### AI·미디어

- 사람 정면·측면·부분 가림의 confidence, FPS, 연속 프레임을 기록합니다.
- 같은 사람 1m·15초 기준 중복 억제 결과를 기록합니다.
- 위치 계산 실패는 `UNKNOWN`으로 처리하고 이벤트 폭증이 없어야 합니다.
- 이벤트 영상에 이전 5초와 이후 10초가 포함되어야 합니다.
- 카메라를 한 번만 열고 AI와 스트림이 동시에 안정적으로 동작해야 합니다.

### 서버·데이터

- 임무 상태 전이가 DB와 로봇 상태에서 일치해야 합니다.
- WebSocket 재연결 후 현재 상태와 누락 telemetry를 복구해야 합니다.
- 두 브라우저가 동시에 제어 lease를 얻을 수 없어야 합니다.
- S3 재시도가 중복 event/object를 만들지 않아야 합니다.
- 재배포 후 PostgreSQL volume 데이터가 유지되어야 합니다.

## 장애 주입 매트릭스

| 주입 | 기대 동작 | 확인할 증적 |
|---|---|---|
| EC2/Wi-Fi 단절 | 자율 탐사 지속, 수동 정지, 로컬 큐 | 정지 시간, queue size, 재연결 sequence |
| S3 차단 | `LOCAL_ONLY`, 탐사 지속, 재업로드 | idempotency key, 최종 READY |
| LiDAR node 종료 | 즉시 정지, PAUSED/ERROR | topic freshness, error code |
| gamepad 분리 | 500ms 이내 정지 | browser·server·Jetson timestamp |
| 카메라 분리 | AI 중단 경고, 영상 없는 수동 금지 | health transition, UI 상태 |
| Backend 재시작 | Jetson 재연결과 상태 동기화 | last ACK sequence, 중복 수 |

## 성능 기록 형식

```text
timestamp, mission_id, component,
latency_ms, fps, cpu_pct, gpu_pct, memory_mb,
jetson_temp_c, dropped_frames, network_rtt_ms,
state, error_code
```

모든 결과에는 다음 메타데이터가 필요합니다.

- Git commit
- 테스트 ID와 일시
- 하드웨어·펌웨어·모델 버전
- 환경과 네트워크 조건
- 실행 명령과 설정 파일 hash
- 기대 결과와 실제 결과
- 로그·CSV·그래프·영상 위치
- 실패 원인, 안전 영향, 후속 Jira

## 결과 기록 템플릿

```markdown
# TEST-ID 시험 이름

- Commit:
- Date/Operator:
- Hardware/Firmware:
- Environment:
- Command:
- Expected:
- Actual:
- Result: PASS | FAIL | BLOCKED
- Safety impact:
- Evidence:
- Follow-up issue:
```

결과 문서는 `docs/testing/results/YYYY-MM-DD-test-id.md` 형식을 권장하며 대용량 영상은 S3 또는 팀 합의 저장소에 두고 링크와 checksum만 기록합니다.

## 시연 완료 기준

- 전체 시나리오를 3회 연속 성공합니다.
- 로컬 영상 지연 목표 500ms 이하를 같은 측정법으로 기록합니다.
- gamepad 또는 heartbeat 단절 후 500ms 이내 정지를 증명합니다.
- EC2 단절 중 로컬 자율성과 재연결 복구를 증명합니다.
- LiDAR·모터 핵심 장애에서 정지하는 것을 증명합니다.
- 임무, 시계열, 이벤트, 지도와 미디어 이력을 조회합니다.

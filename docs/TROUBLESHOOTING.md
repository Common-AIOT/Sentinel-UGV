# Sentinel UGV 트러블슈팅 인덱스

이 문서는 증상에서 상세 진단 절차로 빠르게 이동하기 위한 인덱스다. 수치·상태·복구
정책의 규범은 링크된 통합 명세와 패키지 README이며, 이 문서에서 별도 값을 정의하지
않는다.

## 먼저 지킬 것

1. 예상하지 못한 주행·조향이 있으면 **관제에서 임무를 정지**하고 차량을 들어 바퀴를 띄운다. 수동 조종 중이면 폰 입력을 놓으면 250ms TTL로 멈춘다. 그래도 멈추지 않으면 **12V 모터 배터리 연결을 분리한다** — 전용 E-Stop 스위치는 도입하지 않았으므로 이것이 유일한 하드웨어 차단이다([34-10](03-제어-캘리브레이션.md#34-10-물리-e-stop전원-안전)).
2. 장애 뒤 모터를 자동 재개하지 않는다. 원인을 확인하고 `SAFE_IDLE`에서 명시적으로 시작한다.
3. 재기동 전에 중복 스택과 systemd 상태를 확인한다.

```bash
systemctl status sentinel-demo --no-pager
./scripts/demo_down.sh --dry-run
ros2 node list
ros2 topic echo /diagnostics --once
```

운영 원칙과 전체 장애별 재개 조건은 [37장 운영·복구](06-테스트-보안-운영.md#37-운영모니터링장애-복구-설계)와
[장애별 Runbook](06-테스트-보안-운영.md#37-8-장애별-복구-runbook)을 따른다.

## 증상별 빠른 경로

| 증상 | 먼저 확인 | 다음 조치·상세 근거 |
|---|---|---|
| 스택을 내렸는데 다시 뜬다 | `systemctl status sentinel-demo`, `journalctl -u sentinel-demo -n 100` | systemd가 active면 `demo_down.sh`가 서비스를 통해 내려야 한다. [37-3 systemd](06-테스트-보안-운영.md#37-3-systemd-서비스-구조) |
| 노드는 살아 있는데 임무·MQTT·시리얼이 이상하다 | `./scripts/demo_down.sh --dry-run`, launch 부모와 `install/sentinel_*/lib/` 프로세스 | 중복 스택을 전부 내린 뒤 한 번만 기동한다. [37-3-2 중복 스택](06-테스트-보안-운영.md#37-3-2-스택-두-벌이-겹치면-증상이-주행이-안-된다로-나온다-2026-08-07-s15p11a301-338) |
| ESP32 토픽이 조용하다 | `/diagnostics`의 `MOTOR_LINK`: `rx_frame_count`, `parse_error_count`, `handshake_ok` | 수신 0이면 전원·USB·펌웨어, parse 오류면 보레이트·프로토콜·보드 역할을 본다. [ESP32 진단표](06-테스트-보안-운영.md#esp32-링크가-조용할-때-diagnostics-를-먼저-읽는다-s15p11a301-323) |
| 자율 명령이 있는데 바퀴가 돌지 않는다 | `/cmd_vel_nav` → `/cmd_vel_muxed` → `/cmd_vel_smoothed` → `/cmd_vel_safe` → `/cmd_vel` 순서, `/mission/status` | 기본값에서 Nav2·탐사·안전·EKF가 꺼져 있는지와 safety gate 차단 사유를 확인한다. [현재 ROS 그래프](04-자율주행.md#82-ros2-노드-구성) |
| 0.15m/s 부근에서 정지하거나 매우 느리다 | 명령 속도와 `~/drive_command`, PWM | 고장이 아니라 정지 마찰 구간이다. 순항 0.30m/s, 접근 0.25m/s 운용점을 사용한다. [속도 실측](03-제어-캘리브레이션.md#기본-속도-030-확정--매핑은-고치지-않는다-2026-08-09-s15p11a301-342) |
| 조향은 끝까지 가는데 경로를 못 따른다 | `steering_clamp_count`, 서보 55°/바퀴 22° 비, Smac `minimum_turning_radius=1.8` | 제자리 회전 명령을 쓰지 않고 링키지·히스테리시스를 실측한다. [조향 보정](03-제어-캘리브레이션.md#35-3-엔코더바퀴조향-보정) |
| Nav2가 후방·측면 목표를 계획하지 못한다 | 목표 주변 `/global_costmap/costmap`, `allow_unknown=false` | `/map` 자유 셀이 아니라 global costmap 비용을 확인한다. 현행 planner는 Smac Hybrid-A* REEDS_SHEPP다. [Nav2 구성](04-자율주행.md#24-nav2-경로-계획장애물-회피안전-속도-설계) |
| 지도 또는 관제 화살표가 어긋난다 | `/tf`, `/tf_static`, `/pose`, `/pose/fused`, `map→odom→base_footprint` | `/pose/fused`가 1초 이상 없으면 `/pose`로 폴백한다. IMU yaw와 map 위치를 필드 단위로 섞지 않는다. [관제 pose](04-자율주행.md) |
| 영상이 없거나 지연된다 | `/camera/image_raw/compressed` 29.93FPS, stream node, MediaMTX 로그 | 카메라는 `usb_cam`만 열어야 한다. 관제 인코딩은 15FPS·1500kbps다. [32장 영상 경로](05-통신-서버-영상.md#32-영상-스트리밍3초-링-버퍼이벤트-녹화-설계) |
| 사람은 보이는데 encounter가 안 생긴다 | `/perception/person_candidates`의 `trackId`, `confidence`, 약 1초 관측; Mission Manager 상태 | 탐지 노드는 후보만 내고 encounter는 Mission Manager만 발행한다. [25장 탐지](07-AI-탐지.md#25-사람-탐지추적위치-추정-상세-설계) |
| 사람 지도 위치가 `null`이다 | 후보의 `position` | 현재 정상적인 제한이다. `human_localizer`가 미구현이며 encounter pose는 로봇 위치다. [사람 위치 품질](07-AI-탐지.md#93-사람-위치-품질) |
| 음성이 무음이거나 서버 실패가 무응답으로 보인다 | PulseAudio 기본 소스가 BRIO인지, ASR `/health`, 오류 `code` | 장치·서버 오류를 `NO_VOICE_DETECTED`로 바꾸지 않는다. [33.11 장애 분류](08-AI-음성.md#3311-장애-분류) |
| MQTT·DB·S3 단절 뒤 데이터가 안 올라간다 | Outbox 상태, backend/DB 로그, `pending` 디렉터리 | 자동 주행은 재개하지 말고 상태 동기화와 멱등 재전송을 확인한다. [37-10 Outbox·미디어 복구](06-테스트-보안-운영.md#37-10-outbox와-미디어-복구) |
| 디스크가 찬다 | `df -h`, `/var/lib/sentinel/media/pending` | 링 버퍼는 약 1.6MB 고정이다. 업로드 대기 MP4·rosbag·임시파일을 확인한다. [링 버퍼와 저장](05-통신-서버-영상.md#32-5-3초-링-버퍼-구현) |

## 현재 고장이 아닌 알려진 제한

- `RETURNING`, Frontier 소진·7분 자동 종료, 사람 map 위치는 미구현이다.
- 전·후방 초음파는 관측되지만 보호 정지 발동은 꺼져 있다.
- API·MediaMTX path·Foxglove 읽기에는 애플리케이션 인증이 없다.
- **래칭형 물리 E-Stop 스위치는 도입하지 않았다.** 하드웨어 차단은 12V 배터리 연결 분리뿐이다.
- ROS 경로의 GMS gate 차단 시 의도한 오류 대신 오디오 오류로 보고되는 알려진 결함이 있다.
- 구동 속도와 조향은 외부 엔코더 폐루프 PID가 아니라 실측 매핑 기반 개루프다.

남은 항목과 완료된 실측의 경계는 [TBD 대장](TBD.md)을 기준으로 한다.

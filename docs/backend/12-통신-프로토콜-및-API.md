<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 12. 통신 프로토콜 및 API [확정]
## 12.1 통신 채널
| **채널**         | **용도**                                 | **특성**                  |
|------------------|------------------------------------------|---------------------------|
| MQTT 5 over TLS  | Jetson↔EC2 상태, 이벤트, 명령, ACK      | QoS·retain·LWT·재연결     |
| REST/HTTPS       | 임무·이력 조회, 미디어 업로드 준비       | 요청-응답, 멱등성         |
| STOMP over WSS   | Next.js↔Spring 실시간 상태·제어 중계     | 브라우저 구독·heartbeat   |
| WebRTC/WHEP      | 영상 스트리밍                            | 저지연, 영상 경로 분리    |
| S3 Presigned URL | Jetson의 파일 직접 업로드/브라우저 조회  | AWS 키 비노출, 제한 시간  |
| ROS2 DDS         | Jetson 내부 노드 간 통신                 | 토픽·서비스·액션          |
| USB CDC          | Jetson↔STM32 목표 속도·엔코더·fault      | CRC·sequence·300ms timeout |

## 12.2 REST API 초안
| **Method** | **Endpoint**                   | **설명**           |
|------------|--------------------------------|--------------------|
| POST       | /api/missions                  | 새 임무 생성       |
| POST       | /api/missions/{id}/start       | 탐사 시작          |
| POST       | /api/missions/{id}/pause       | 일시정지           |
| POST       | /api/missions/{id}/resume      | 탐사 재개          |
| POST       | /api/missions/{id}/return      | 복귀               |
| POST       | /api/missions/{id}/stop        | 탐사 종료          |
| GET        | /api/missions                  | 임무 목록          |
| GET        | /api/missions/{id}             | 임무 상세          |
| GET        | /api/missions/{id}/telemetry   | 시계열 조회        |
| GET        | /api/missions/{id}/events      | 이벤트 조회        |
| POST       | /api/robots/{id}/estop         | 소프트웨어 E-Stop  |
| POST       | /api/robots/{id}/estop/release | E-Stop 해제        |
| POST       | /api/media/presign-upload      | S3 업로드 URL 발급 |
| GET        | /api/media/{id}/presign-view   | S3 조회 URL 발급   |
| GET        | /api/robots/{id}/health        | 장치 상태 조회     |

## 12.3 WebSocket 메시지 공통 필드
```json
{
"type": "TELEMETRY | ROBOT_STATUS | ENCOUNTER | CONTROL_COMMAND | CONTROL_ACK | SYSTEM_ERROR",
"messageId": "uuid",
"robotId": "sentinel-01",
"missionId": "uuid-or-null",
"sequence": 152,
"sentAt": "2026-07-17T10:30:00.123+09:00",
"payload": { }
}
```

## 12.4 수동 주행 명령 예시
```json
{
"type": "CONTROL_COMMAND",
"messageId": "cmd-uuid",
"robotId": "sentinel-01",
"sequence": 152,
"sentAt": "2026-07-17T10:30:00.123+09:00",
"payload": {
"command": "DRIVE",
"mode": "MANUAL",
"linearX": 0.25,
"angularZ": -0.35,
"deadman": true,
"ttlMs": 250
}
}
```

## 12.5 제어 ACK 예시
```json
{
"type": "CONTROL_ACK",
"messageId": "ack-uuid",
"sequence": 152,
"payload": {
"commandId": "cmd-uuid",
"accepted": true,
"appliedLinearX": 0.22,
"appliedAngularZ": -0.30,
"robotState": "MANUAL",
"reasonCode": null
}
}
```

## 12.6 오류 코드 초안
| **코드**             | **의미**                | **처리**                 |
|----------------------|-------------------------|--------------------------|
| DEVICE_OFFLINE       | Jetson 연결 끊김        | 제어 비활성화, 상태 경고 |
| LIDAR_UNAVAILABLE    | LiDAR 데이터 없음       | 자율주행 중단·정지       |
| LOCALIZATION_LOST    | 위치 신뢰도 저하        | PAUSED 또는 ERROR        |
| MOTOR_NO_RESPONSE    | 모터 드라이버 응답 없음 | ESTOP                    |
| GAMEPAD_DISCONNECTED | 게임패드 연결 해제      | 수동 즉시 정지           |
| CONTROL_LEASE_DENIED | 다른 세션이 제어권 보유 | 조회 전용                |
| S3_UPLOAD_PENDING    | 파일 업로드 대기        | 로컬 보관 후 재시도      |
| STREAM_LOCAL_FAILED  | 로컬 스트림 실패        | REMOTE 전환              |

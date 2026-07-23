<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 27. Spring Boot 관제 백엔드·도메인·API 상세 설계

## 27.1 책임 경계

Spring Boot는 로봇을 실시간 폐루프로 제어하지 않는다. 임무·운영자·명령 이력·실시간 관제 전달·데이터 저장을 담당하며, 안전 판단과 최종 모터 권한은 Jetson·STM32에 남긴다.

## 27.2 모듈 구조

```text
backend/
├─ mission/          # 임무 생성·상태·요약
├─ robot/            # 등록·presence·health
├─ control/          # Lease·명령·ACK·감사 로그
├─ encounter/        # 피해자 발견·응답·미디어 연결
├─ telemetry/        # TimescaleDB 쓰기·조회
├─ media/            # S3 Presigned URL·상태
├─ realtime/         # STOMP/WebSocket 브로드캐스트
├─ messaging/        # MQTT 구독·발행·멱등 처리
├─ security/         # 인증·권한·감사
└─ common/           # 오류·시간·ID·검증
```

## 27.3 핵심 도메인 규칙

- 한 로봇에는 동시에 하나의 활성 임무만 존재한다.
- 한 로봇에는 동시에 하나의 유효 control lease만 존재한다.
- 로봇이 보낸 임무 상태와 서버 명령 상태를 분리해 저장한다.
- 이벤트는 `messageId` 또는 도메인 id의 unique constraint로 중복 삽입을 막는다.
- S3 업로드 성공 전에도 DB 이벤트는 `LOCAL_ONLY/PENDING`으로 조회 가능하다.
- 서버가 임의로 `COMPLETED`를 만들지 않고 로봇의 종료 결과와 증빙을 확인한다.

## 27.4 REST API 기준

| Method | Endpoint | 목적 |
|---|---|---|
| POST | `/api/v1/missions` | 임무 생성 |
| POST | `/api/v1/missions/{id}/commands` | start/pause/resume/return/stop |
| GET | `/api/v1/missions` | 임무 목록 |
| GET | `/api/v1/missions/{id}` | 임무 상세·요약 |
| GET | `/api/v1/missions/{id}/trajectory` | 경로 다운샘플 조회 |
| GET | `/api/v1/missions/{id}/telemetry` | 시계열 범위 조회 |
| GET | `/api/v1/missions/{id}/encounters` | 피해자 발견 목록 |
| POST | `/api/v1/robots/{id}/control-leases` | 제어권 요청 |
| DELETE | `/api/v1/robots/{id}/control-leases/{leaseId}` | 제어권 반납 |
| POST | `/api/v1/media/presign-upload` | 업로드 URL 발급 |
| GET | `/api/v1/media/{id}/view-url` | 단기 조회 URL 발급 |

제어 API가 HTTP 202를 반환한 것은 로봇이 동작을 완료했다는 뜻이 아니다. 응답에는 `commandId`와 `ACCEPTED_FOR_DELIVERY`를 반환하고, 실제 수락·실행 결과는 ACK 상태로 갱신한다.

## 27.5 오류 응답

```json
{
  "code": "CONTROL_LEASE_DENIED",
  "message": "다른 운영자가 제어권을 보유 중입니다.",
  "traceId": "uuid",
  "occurredAt": "2026-07-22T06:30:00Z"
}
```

내부 예외·AWS 키·SQL 문장은 클라이언트에 노출하지 않는다.

## 27.6 백엔드 인수 기준

- MQTT QoS 1 중복 메시지를 2회 받아도 DB 행과 웹 이벤트는 1회만 생성된다.
- 명령 요청부터 ACK까지 상태가 추적된다.
- 비활성·만료 Lease의 DRIVE 명령을 거부한다.
- 임무 상세에서 경로·시계열·encounter·미디어를 함께 조회할 수 있다.
- DB·S3 일부 장애가 다른 API 전체를 무한 대기시키지 않는다.

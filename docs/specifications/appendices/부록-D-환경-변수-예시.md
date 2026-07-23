<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 D. 환경 변수 예시
```bash
# frontend
NEXT_PUBLIC_API_BASE_URL=https://sentinel.example.com/api
NEXT_PUBLIC_WS_URL=wss://sentinel.example.com/ws
NEXT_PUBLIC_LOCAL_STREAM_URL=https://sentinel.local:8889/robot/whep
NEXT_PUBLIC_REMOTE_STREAM_URL=https://stream.example.com/robot/whep
# backend
DB_URL=jdbc:postgresql://postgres:5432/sentinel
DB_USER=sentinel
DB_PASSWORD=***
S3_BUCKET=sentinel-ugv-assets
AWS_REGION=ap-northeast-2
CONTROL_LEASE_TTL_MS=3000
MQTT_BROKER_URL=ssl://mqtt.example.com:8883
# jetson
ROBOT_ID=sentinel-01
MQTT_BROKER_URL=ssl://mqtt.example.com:8883
STM32_DEVICE=/dev/sentinel_mcu
EVENT_PRE_SECONDS=3
EVENT_POST_SECONDS=3
MANUAL_CMD_TTL_MS=250
STM32_WATCHDOG_MS=300
```

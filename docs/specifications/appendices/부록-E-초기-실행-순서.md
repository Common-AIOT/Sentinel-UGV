<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 E. 초기 실행 순서
1. JetPack `6.2.1+b38`·Ubuntu 22.04·ROS 2 Humble 확인
2. 물리 E-Stop·STM32 SAFE_IDLE·300ms watchdog 시험
3. BRIO 100·YDLIDAR·IMU 장치 인식과 udev 별칭 확인
4. 차량을 띄운 상태에서 엔코더 방향·모터 PID 시험
5. ROS 2 TF와 `/scan`, `/odom` 시각화
6. YOLO26n person·ByteTrack 시험
7. Spring Boot·Next.js·PostgreSQL/TimescaleDB·MQTT·MediaMTX 실행
8. 가짜 텔레메트리와 MQTT/WSS 재연결 확인
9. SLAM 수동 주행 → Nav2 목표점 → Frontier 순으로 검증
10. encounter·안전 접근·음성·이벤트 영상·S3/Outbox 시험
11. 복귀·E-Stop·네트워크/USB 장애 시험
12. 전체 시나리오 3회 연속 수행

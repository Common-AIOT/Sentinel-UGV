<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 G. 용어집
| **용어**           | **설명**                                                    |
|--------------------|-------------------------------------------------------------|
| **UGV**            | Unmanned Ground Vehicle, 무인 지상 차량                     |
| **온디바이스 AI**  | 클라우드가 아닌 장치 내부에서 AI 추론을 수행하는 방식       |
| **추론**           | 학습된 모델에 새 입력을 넣어 결과를 얻는 과정               |
| **SLAM**           | 위치 추정과 지도 작성을 동시에 수행하는 기술                |
| **Nav2**           | ROS2 기반 경로 계획·주행·복구 프레임워크                    |
| **Frontier**       | 확인된 자유 공간과 미지 공간의 경계                         |
| **오도메트리**     | 바퀴·IMU 등으로 로봇 이동량을 추정한 값                     |
| **TF**             | ROS에서 좌표계 관계를 관리하는 시스템                       |
| **Costmap**        | 장애물과 이동 비용을 표현하는 격자 지도                     |
| **WebRTC**         | 브라우저 저지연 실시간 미디어 통신 기술                     |
| **WHEP**           | WebRTC 스트림 수신을 위한 HTTP 기반 표준 인터페이스         |
| **MediaMTX**       | RTSP/WebRTC 등 미디어 프로토콜 중계 서버                    |
| **TimescaleDB**    | PostgreSQL 확장 기반 시계열 데이터베이스                    |
| **MQTT**           | 차량 상태·이벤트·명령을 토픽 기반으로 교환하는 메시지 프로토콜 |
| **Encounter**      | 한 번의 사람 그룹 발견·접근·대화·보고를 묶는 도메인 단위     |
| **ByteTrack**      | 영상 프레임 간 사람별 track ID를 유지하는 다중 객체 추적기    |
| **Watchdog**       | 정해진 시간 안에 정상 신호가 없으면 안전 동작을 수행하는 감시기 |
| **S3**             | AWS 객체 스토리지                                           |
| **Presigned URL**  | 제한 시간 동안 특정 S3 객체 업로드·조회 권한을 제공하는 URL |
| **Deadman switch** | 버튼을 누르는 동안에만 장비가 동작하도록 하는 안전 입력     |
| **E-Stop**         | 비상 정지                                                   |
| **TBD**            | 추후 결정할 항목                                            |

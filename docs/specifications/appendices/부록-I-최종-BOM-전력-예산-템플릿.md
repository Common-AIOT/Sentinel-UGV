<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 I. 최종 BOM·전력 예산 템플릿

| 분류 | 부품·모델 | 수량 | 정격·인터페이스 | 공급처 | 확정 상태 |
|---|---|---:|---|---|---|
| 컴퓨팅 | Jetson Orin Nano 8GB | 1 | JetPack 6.2.1+b38 | 제공 | 확정 |
| 제어 | STM32 보드 | 1 | 정확한 보드명·I/O TBD | 제공/구매 | 모델 기록 필요 |
| 센서 | YDLIDAR X4 Pro | 1 | USB Serial | 제공 | 잠정 확정 |
| 센서 | Logitech BRIO 100 | 1 | USB, 포맷 실측 | 제공 | 잠정 확정 |
| 구동 | 엔코더 DC 모터 | 2 | 전압·RPM·토크·stall current | TBD | 미확정 |
| 구동 | 듀얼 모터 드라이버 | 1 | 연속·피크 전류 | TBD | 미확정 |
| 전원 | 배터리·퓨즈·DC-DC | 각 1 | 전압·용량·효율 | TBD | 미확정 |
| 안전 | 물리 E-Stop | 1 | 모터 전원 차단 정격 | TBD | 미확정 |
| 음성 | 마이크·스피커 | 각 1 | USB/아날로그·출력 | TBD | 미확정 |

최종 BOM에는 단가뿐 아니라 데이터시트 링크, 무게, 공급 리드타임, 대체품, 실제 측정 전류를 기록한다.

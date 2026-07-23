<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 L. 최종 파라미터 동결표

| 영역 | 파라미터 | 초기값 | 최종값 | 시험 증빙 |
|---|---|---:|---:|---|
| 안전 | Jetson command timeout | 300ms | TBD | CTRL-03 |
| 안전 | STM32 communication watchdog | 300ms | TBD | CTRL-03 |
| 수동 | 브라우저 command TTL | 250ms | TBD | MAN-02 |
| 영상 | encounter pre-buffer | 3s | 3s | VID-03 |
| 영상 | encounter post-buffer | 3s | 3s | VID-03 |
| 주행 | 최대 자율 선속도 | 0.25m/s | TBD | NAV-03 |
| 접근 | 최대 접근 선속도 | 0.10m/s | TBD | SCN-01 |
| 차체 | 엔코더 PPR·거리 스케일 | TBD | TBD | CAL-01~03 |
| 차체 | 유효 트랙 폭 | TBD | TBD | CAL-04 |
| AI | person confidence·안정 관측 시간 | TBD·약 1s | TBD | AI-01~04 |
| Nav2 | inflation·collision distance | TBD | TBD | NAV-03 |
| 짐벌 | 영점·각도 제한·mode | TBD | TBD | CAL-07~08 |

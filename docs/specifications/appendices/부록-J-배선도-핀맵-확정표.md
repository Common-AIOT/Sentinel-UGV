<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 J. 배선도·핀맵 확정표

| From | To | 신호/전원 | 커넥터·핀 | 전압 | 검증자·일자 |
|---|---|---|---|---:|---|
| Jetson | STM32 | USB CDC | `/dev/sentinel_mcu` | USB | TBD |
| STM32 | 좌 엔코더 | A/B | TBD | TBD | TBD |
| STM32 | 우 엔코더 | A/B | TBD | TBD | TBD |
| STM32 | 모터 드라이버 | PWM/DIR/ENABLE | TBD | 3.3V 논리 확인 | TBD |
| E-Stop | 모터 전원 계층 | 전력 차단 | TBD | 배터리 전압 | TBD |
| DC-DC | Jetson | 전원 | 공식 전원 입력 | TBD | TBD |
| DC-DC | 서보 | 전원 | 별도 분기 | TBD | TBD |

배선도는 실제 핀을 확인하기 전까지 추정값을 넣지 않으며, 최종본에는 퓨즈·공통 GND·커넥터 방향·케이블 라벨까지 표시한다.

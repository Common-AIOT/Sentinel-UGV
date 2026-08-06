#pragma once

// 100Hz 제어 태스크(§34-8). 하는 일 셋:
//   1. 300ms 통신 워치독 검사 - 트립 시 구동만 끊고 조향각은 유지한다(§34-7).
//   2. 후륜 방향 전환 데드타임 해제(driveUpdate).
//   3. 조향 서보 슬루레이트 적용·펄스 갱신(steeringUpdate).
void controlTaskFn(void* pvParameters);

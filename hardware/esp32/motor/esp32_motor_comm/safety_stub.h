// 모터 ESP32의 실제 액추에이션 계층. comm_task/control_task가 호출하는 훅이며,
// BTS7960 2개(좌/우 구동)의 PWM/DIR/EN을 직접 제어한다.
//
// 하드웨어 변경(§ 조향 모터·BTS7960 제거, 캐스터 휠로 대체)에 따라 좌·우 구동
// 모터 2개만 남았다 - targetSteeringMdeg는 더 이상 액추에이션에 쓰이지 않고
// 항상 무시된다. 좌·우 차동(tank drive)만으로 전진/후진/조향을 모두 수행한다.
#pragma once

#include <cstdint>

// PWM 채널 attach, EN 핀 초기화. setup()에서 FreeRTOS 태스크 생성 전에 한 번 호출한다.
void motorDriverInit();

void applySafeOutputs();
void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps,
                        int16_t targetSteeringMdeg);

// comm_task의 DRIVE_STATE 송신용 실측 상태 조회.
bool motorDriverEnabled();
int16_t motorDriverAppliedPwmLeft();   // -255..255
int16_t motorDriverAppliedPwmRight();  // -255..255

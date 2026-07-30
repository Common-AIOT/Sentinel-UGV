// BTS7960 PWM 구동은 이번 통신 계층 티켓의 범위 밖이다. 실제 구현 전까지는 이 두
// 함수가 comm_task/control_task가 호출하는 유일한 훅이며, 후속 PWM 티켓이 본문을 채운다.
#pragma once

#include <cstdint>

void applySafeOutputs();
void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps,
                        int16_t targetSteeringMdeg);

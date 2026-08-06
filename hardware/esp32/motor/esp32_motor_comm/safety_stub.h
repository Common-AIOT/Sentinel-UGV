// 후륜 구동 액추에이션 계층. comm_task/control_task가 호출하는 훅이며,
// BTS7960 2개(후륜 좌·우)의 PWM/DIR/EN을 직접 제어한다.
//
// 2026-08-06 하드웨어 변경(전륜 서보 조향 복구)에 따라 이 계층은 **전진·후진만**
// 담당한다. 조향은 전륜 타이로드에 직결된 DS51150 서보가 맡고 그 제어는
// steering.h에 있다. 즉 좌·우 속도 차로 회두를 만들지 않는다 - 조향 링크가 정한
// 조향각과 다투면 타이어·링키지에 무리가 간다(§6.3).
//
// 좌·우 목표는 프로토콜상 분리되어 있고(초기 구성에서는 Jetson이 같은 값을 보낸다,
// §34-2) 전자 차동 보정(TBD-CAL-002)이 붙을 자리로 남겨 둔다. 다만 부호가 서로
// 반대인 명령은 차동 회두 명령이므로 실행하지 않고 양쪽을 0으로 만든다.
#pragma once

#include <cstdint>

// PWM 채널 attach, EN 핀 초기화. setup()에서 FreeRTOS 태스크 생성 전에 한 번 호출한다.
void motorDriverInit();

void applySafeOutputs();

// 새 목표를 등록한다. 방향 반전이 필요하면 즉시 출력을 끄고 데드타임이 지난 뒤
// driveUpdate()가 실제 반전을 적용한다(§34-8 「방향 전환 전 짧은 중립·감속 구간」).
// 조향은 이 함수가 다루지 않는다 - steeringSetTarget()을 별도로 호출한다.
void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps);

// 방향 전환 데드타임 경과를 확인해 보류된 목표를 적용한다. control_task가 100Hz로
// 호출한다. comm_task에서 delay()로 기다리면 그 사이 직렬 수신이 멈춘다.
void driveUpdate(uint32_t nowMs);

// comm_task의 DRIVE_STATE 송신용 실측 상태 조회.
bool motorDriverEnabled();
int16_t motorDriverAppliedPwmLeft();   // -255..255
int16_t motorDriverAppliedPwmRight();  // -255..255

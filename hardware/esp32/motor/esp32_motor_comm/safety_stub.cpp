#include "safety_stub.h"

void applySafeOutputs() {
  // TODO(후속 PWM 티켓): BTS7960 3개 PWM=0, 브레이크, driver enable LOW 처리.
  // 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
  // 추가하지 말 것(README 참고).
}

void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps,
                        int16_t targetSteeringMdeg) {
  (void)targetDriveLeftMmps;
  (void)targetDriveRightMmps;
  (void)targetSteeringMdeg;
  // TODO(후속 PWM 티켓): 좌/우 구동 속도, 조향각 목표를 실제 BTS7960 PWM/DIR로 반영.
}

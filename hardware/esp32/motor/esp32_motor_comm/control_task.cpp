#include "control_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <fault_codes.h>
#include "board_state.h"
#include "safety_stub.h"
#include "steering.h"

namespace {
constexpr uint32_t CONTROL_LOOP_INTERVAL_MS = 10;      // 100Hz (§34-8)
constexpr uint32_t COMMAND_WATCHDOG_TIMEOUT_MS = 300;  // §34-7
}  // namespace

void controlTaskFn(void* pvParameters) {
  (void)pvParameters;
  TickType_t lastWakeTime = xTaskGetTickCount();

  for (;;) {
    uint32_t now = millis();
    bool justTripped = false;

    motorSharedStateUpdate([now, &justTripped](MotorSharedState& s) {
      if (s.state == MotorBoardState::ESTOP_LATCHED || s.state == MotorBoardState::FAULT_LATCHED) {
        return;
      }
      bool alreadyFlagged = (s.faultFlags & FAULT_COMM_TIMEOUT_MOTOR) != 0;
      bool timedOut = s.hasAcceptedSequence && (now - s.lastValidDriveCommandMs > COMMAND_WATCHDOG_TIMEOUT_MS);
      if (timedOut && !alreadyFlagged) {
        s.faultFlags |= FAULT_COMM_TIMEOUT_MOTOR;
        s.targetDriveLeftMmps = 0;
        s.targetDriveRightMmps = 0;
        s.state = MotorBoardState::STOPPING;
        justTripped = true;
      }
    });

    if (justTripped) {
      // 구동만 끊는다. s.targetSteeringMdeg는 그대로 두고 steeringUpdate()가 계속
      // 마지막 목표를 향해 수렴하게 한다 - 통신이 끊겨도 조향은 중립으로 튀지
      // 않는다(§34-7, CTRL-26).
      applySafeOutputs();
    }

    // 방향 전환 데드타임 해제와 조향 슬루레이트는 통신과 무관하게 계속 돌아야
    // 한다. comm_task에서 delay()로 기다리면 그 사이 직렬 수신이 멈춘다.
    driveUpdate(now);
    steeringUpdate(now);

    vTaskDelayUntil(&lastWakeTime, pdMS_TO_TICKS(CONTROL_LOOP_INTERVAL_MS));
  }
}

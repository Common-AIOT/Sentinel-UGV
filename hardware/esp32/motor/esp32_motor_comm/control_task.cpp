#include "control_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <fault_codes.h>
#include "board_state.h"
#include "safety_stub.h"

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
      applySafeOutputs();
    }

    vTaskDelayUntil(&lastWakeTime, pdMS_TO_TICKS(CONTROL_LOOP_INTERVAL_MS));
  }
}

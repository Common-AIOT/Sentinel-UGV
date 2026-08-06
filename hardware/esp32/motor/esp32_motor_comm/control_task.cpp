#include "control_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <fault_codes.h>
#include "board_state.h"
#include "mode_arbiter.h"
#include "safety_stub.h"
#include "steering.h"

// 이 태스크가 **유일한 액추에이터**다 (S15P11A301-298).
//
// 종전에는 comm_task(prio 2)와 여기(prio 3)가 둘 다 safety_stub·steering 의
// 무보호 file-static 을 건드렸다. 거기에 core 0 의 HTTP 핸들러가 세 번째 writer 로
// 들어오면 잠재된 동일코어 경쟁이 진짜 크로스코어 경쟁이 된다.
//
// 그래서 명령 수신 계층들은 뮤텍스 아래에서 **의도만 기록**하고, 바퀴 소유자를
// 결정하는 10ms 틱은 정확히 여기 하나다. 겹침 창이 구조적으로 사라진다.

namespace {
constexpr uint32_t CONTROL_LOOP_INTERVAL_MS = 10;  // 100Hz (§34-8)
}  // namespace

void controlTaskFn(void* pvParameters) {
  (void)pvParameters;
  TickType_t lastWakeTime = xTaskGetTickCount();

  for (;;) {
    const uint32_t now = millis();

    DriveDecision decision{};
    motorSharedStateUpdate([now, &decision](MotorSharedState& s) {
      decision = arbitrateDrive(s, now);

      if (decision.applyNextState) {
        s.state = decision.nextState;
      }
      if (decision.raiseCommTimeout) {
        s.faultFlags |= FAULT_COMM_TIMEOUT_MOTOR;
      } else if (decision.clearCommTimeout) {
        s.faultFlags &= ~FAULT_COMM_TIMEOUT_MOTOR;
      }
      if (decision.owner == DriveOwner::JETSON) {
        // 실제로 걸었을 때만 appliedSequence 를 옮긴다. 수동 래치 중에는 여기
        // 오지 않으므로 값이 동결되고, DRIVE_STATE 가 거짓말하지 않는다.
        s.lastAppliedSequence = s.lastAcceptedSequence;
        s.hasAppliedSequence = s.hasAcceptedSequence;
      }
      // 수동 분기에서 구동을 0 으로 만드는 것은 액추에이션 계층의 몫이지만,
      // 장부 쪽 목표도 함께 0 으로 두어야 진단이 사실과 맞는다.
      if (decision.owner != DriveOwner::JETSON) {
        s.targetDriveLeftMmps = 0;
        s.targetDriveRightMmps = 0;
      }
    });

    if (decision.owner == DriveOwner::NONE) {
      // 구동만 끊는다. 조향 목표는 그대로 두고 steeringUpdate() 가 계속 마지막
      // 목표를 향해 수렴하게 한다 - 통신이 끊겨도 조향은 중립으로 튀지 않는다
      // (§34-7, CTRL-26).
      applySafeOutputs();
    } else {
      // 조향을 먼저 반영한다. 정지 중 조향 금지 판정(§34-2)에 쓰는 선속도는 같은
      // 결정의 구동 목표이므로, 구동을 적용하기 전에 판단해야 한다.
      if (decision.applySteering) {
        const bool steeringAccepted = steeringSetTarget(
            decision.steeringMdeg, decision.steeringRateMdps,
            decision.steeringDriveMagnitudeMmps);
        if (decision.reportSteeringFault) {
          // §34-9 bit 14. 래치하지 않는다 - 정상 명령이 들어오면 즉시 내린다.
          // 조향에는 다른 진단 창구가 없으므로 "지금 클램프·거부되고 있다"를
          // 실시간으로 보여야 하고, 래치하면 그 구분이 사라진다.
          //
          // **수동 경로는 여기 오지 않는다.** 정지 중 좌우 누름은 애초에
          // applySteering=false 로 걸러져, 사람이 조종할 때마다 bit 14 가 깜박여
          // 그 비트의 의미("젯슨이 무효한 것을 보냈다")가 파괴되는 일이 없다.
          motorSharedStateUpdate([steeringAccepted](MotorSharedState& s) {
            if (steeringAccepted) {
              s.faultFlags &= ~FAULT_STEERING_COMMAND_INVALID;
            } else {
              s.faultFlags |= FAULT_STEERING_COMMAND_INVALID;
            }
          });
        }
      }
      applyDriveTargets(decision.driveLeftMmps, decision.driveRightMmps);
    }

    // 방향 전환 데드타임 해제와 조향 슬루레이트는 통신과 무관하게 계속 돌아야
    // 한다. comm_task에서 delay()로 기다리면 그 사이 직렬 수신이 멈춘다.
    driveUpdate(now);
    steeringUpdate(now);

    vTaskDelayUntil(&lastWakeTime, pdMS_TO_TICKS(CONTROL_LOOP_INTERVAL_MS));
  }
}

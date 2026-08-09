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
constexpr TickType_t CONTROL_LOOP_INTERVAL_TICKS =
    pdMS_TO_TICKS(CONTROL_LOOP_INTERVAL_MS);
}  // namespace

void controlTaskFn(void* pvParameters) {
  (void)pvParameters;
  TickType_t lastWakeTime = xTaskGetTickCount();

  for (;;) {
    const uint32_t now = millis();

    DriveDecision decision{};
    // 캘리브레이션 의도는 뮤텍스 안에서 읽어 오고, steering.* 호출은 뮤텍스 밖에서
    // 한다. HTTP 태스크는 필드만 쓰고 액추에이터는 끝까지 이 태스크 하나다.
    uint8_t centerDeg = 0;
    uint8_t maxOffsetDeg = 0;
    bool armed = false;
    bool jogPending = false;
    int16_t jogMdeg = 0;

    motorSharedStateUpdate([now, &decision, &centerDeg, &maxOffsetDeg, &armed,
                            &jogPending, &jogMdeg](MotorSharedState& s) {
      decision = arbitrateDrive(s, now);

      // ---- 임시 진단 (S15P11A301-339) ----
      // arbitrateDrive() 내부의 jetsonStale 판정을 그대로 다시 계산한다(부작용
      // 없는 순수 조건이라 중복 계산이 안전하다). owner 만으로는 "AUTO_ACTIVE로
      // 보고되지만 실제로는 stale 분기로 새서 owner=NONE" 인 경우와 진짜
      // owner=JETSON 인데 목표가 0인 경우를 구분할 수 없어서 둘 다 싣는다.
      // board_state.h 필드 설명 참고. 원인 확인되면 이 블록 전체를 제거한다.
      s.debugJetsonStale = s.hasAcceptedSequence &&
          (now - s.lastValidDriveCommandMs > JETSON_WATCHDOG_TIMEOUT_MS);
      s.debugDriveOwner = (uint8_t)decision.owner;
      s.debugDecisionDriveLeftMmps = decision.driveLeftMmps;
      s.debugDecisionDriveRightMmps = decision.driveRightMmps;

      centerDeg = s.servoCenterDeg;
      maxOffsetDeg = s.servoMaxOffsetDeg;
      armed = s.servoArmed;
      // 1회성 요청이므로 여기서 소비한다. 다음 틱에 또 걸리면 안 된다.
      jogPending = s.servoJogPending;
      jogMdeg = s.servoJogMdeg;
      s.servoJogPending = false;

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

    // 매핑을 먼저 갱신한다. 아래 steeringSetTarget·steeringUpdate 가 새 중립과
    // 새 게인으로 계산하도록 순서를 지킨다.
    steeringApplyCalibration(centerDeg, maxOffsetDeg, armed);

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

    // 조그는 **주행 목표를 반영한 뒤**에 건다. 순서가 반대면 정지 상태에서
    // applySteering=false 로 걸러진 조향이 조그를 덮어써 아무 일도 일어나지 않는다.
    // manual_web 이 구동 0 일 때만 요청을 받으므로 여기서 다시 검사하지 않는다.
    if (jogPending) {
      steeringJogToMdeg(jogMdeg);
    }

    // 방향 전환 데드타임 해제와 조향 슬루레이트는 통신과 무관하게 계속 돌아야
    // 한다. comm_task에서 delay()로 기다리면 그 사이 직렬 수신이 멈춘다.
    driveUpdate(now);
    steeringUpdate(now);

    // vTaskDelayUntil()은 마감 시각을 이미 넘긴 경우 잠들지 않고 즉시 반환한다.
    // 제어 계산이 10ms보다 길어진 상태가 계속되면 우선순위가 낮은 comm_task가
    // 영구 기아에 빠져 UART가 완전히 멎는다. 정상 주기에는 절대주기 100Hz를
    // 유지하고, 마감을 놓친 주기에만 최소 1틱을 양보해 안전 통신을 보장한다.
    const TickType_t nextWakeTime = lastWakeTime + CONTROL_LOOP_INTERVAL_TICKS;
    const TickType_t beforeDelay = xTaskGetTickCount();
    const bool deadlineMissed =
        (int32_t)(beforeDelay - nextWakeTime) >= 0;
    vTaskDelayUntil(&lastWakeTime, CONTROL_LOOP_INTERVAL_TICKS);
    if (deadlineMissed) {
      vTaskDelay(1);
    }
  }
}

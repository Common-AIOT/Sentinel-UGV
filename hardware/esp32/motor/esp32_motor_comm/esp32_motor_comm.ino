// 모터 ESP32 <-> Jetson USB 직렬 통신 계층.
// 범위: COBS+CRC16 프레이밍, HELLO/HELLO_ACK, STOP/ESTOP, 300ms 통신 워치독,
// DRIVE_STATE/DIAGNOSTIC 송신, BTS7960 2개(좌/우 구동) 실제 PWM 구동(safety_stub.h 참고).
// 조향 모터는 캐스터 휠로 대체되어 제거되었다.
//
// 기존 motor_test/double_motor_test/triple_motor_test 벤치 스케치는 그대로 둔다.
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "board_state.h"
#include "comm_task.h"
#include "control_task.h"
#include "safety_stub.h"

constexpr uint32_t COMM_TASK_STACK_WORDS = 4096;
constexpr uint32_t CONTROL_TASK_STACK_WORDS = 2048;
constexpr UBaseType_t COMM_TASK_PRIORITY = 2;
// 통신 워치독 검사가 시간에 더 민감하므로 comm_task보다 우선순위를 높게 둔다.
constexpr UBaseType_t CONTROL_TASK_PRIORITY = 3;

void setup() {
  // §34-6: 전원 인가 직후 PWM=0·driver enable=LOW를 태스크 생성보다 먼저 보장한다.
  motorDriverInit();
  motorSharedStateInit();

  xTaskCreatePinnedToCore(commTaskFn, "comm_task", COMM_TASK_STACK_WORDS, nullptr,
                           COMM_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(controlTaskFn, "control_task", CONTROL_TASK_STACK_WORDS, nullptr,
                           CONTROL_TASK_PRIORITY, nullptr, 1);
}

void loop() {
  // 모든 동작은 FreeRTOS 태스크(comm_task, control_task)가 담당한다.
  vTaskDelay(pdMS_TO_TICKS(1000));
}

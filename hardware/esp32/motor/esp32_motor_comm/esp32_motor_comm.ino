// 모터 ESP32 <-> Jetson USB 직렬 통신 계층.
// 범위: COBS+CRC16 프레이밍, HELLO/HELLO_ACK, STOP/ESTOP, 300ms 통신 워치독,
// DRIVE_STATE/DIAGNOSTIC 송신, BTS7960 2개(후륜 좌·우 구동) 전·후진 PWM 구동
// (safety_stub.h)과 전륜 DS51150 조향 서보 PWM 생성(steering.h).
//
// 2026-08-06 하드웨어 변경: 앞쪽 캐스터 2개를 제거하고 전륜 조향부를 복구했다.
// 후륜 DC 모터 2개는 전·후진 전용, 조향은 전륜 서보 1개 담당이다(§6.3, §34-2).
//
// 기존 motor_test/double_motor_test/triple_motor_test/steering_servo_test 벤치
// 스케치는 그대로 둔다.
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "board_state.h"
#include "comm_task.h"
#include "control_task.h"
#include "safety_stub.h"
#include "steering.h"

constexpr uint32_t COMM_TASK_STACK_WORDS = 4096;
constexpr uint32_t CONTROL_TASK_STACK_WORDS = 2048;
constexpr UBaseType_t COMM_TASK_PRIORITY = 2;
// 통신 워치독 검사가 시간에 더 민감하므로 comm_task보다 우선순위를 높게 둔다.
constexpr UBaseType_t CONTROL_TASK_PRIORITY = 3;

void setup() {
  // §34-6: 전원 인가 직후 PWM=0·driver enable=LOW·조향 중립(δ=0)을 태스크 생성보다
  // 먼저 보장한다. 조향만 중립으로 시작하는 것은 재부팅 직후에 믿을 수 있는 마지막
  // 각도가 없기 때문이며(RAM이 지워졌고 실제 바퀴 각도를 읽을 수단이 없다), 이후
  // 모든 정지 경로에서는 각도를 유지한다(§34-7).
  motorDriverInit();
  steeringInit();
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

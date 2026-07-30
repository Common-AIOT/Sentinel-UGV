// 센서 ESP32 <-> Jetson USB 직렬 통신 계층.
// 범위: COBS+CRC16 프레이밍, HELLO/HELLO_ACK, ENCODER/ENVIRONMENT/PROXIMITY_STATE 송신
// (placeholder 값), 300ms 통신 워치독(로컬 액추에이터가 없어 STALE 보고만). 실제
// MT6701/DHT-11/HC-SR04 판독은 후속 티켓(sensor_task.h 참고).
//
// 기존 encoder/total 벤치 스케치(mt6701_test_sample, total_sensor_test 등)는 그대로 둔다.
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "board_state.h"
#include "comm_task.h"
#include "sensor_task.h"

constexpr uint32_t COMM_TASK_STACK_WORDS = 4096;
constexpr uint32_t SENSOR_TASK_STACK_WORDS = 2048;
constexpr UBaseType_t COMM_TASK_PRIORITY = 2;
constexpr UBaseType_t SENSOR_TASK_PRIORITY = 1;

void setup() {
  sensorSharedStateInit();

  xTaskCreatePinnedToCore(commTaskFn, "comm_task", COMM_TASK_STACK_WORDS, nullptr,
                           COMM_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(sensorTaskFn, "sensor_task", SENSOR_TASK_STACK_WORDS, nullptr,
                           SENSOR_TASK_PRIORITY, nullptr, 1);
}

void loop() {
  // 모든 동작은 FreeRTOS 태스크(comm_task, sensor_task)가 담당한다.
  vTaskDelay(pdMS_TO_TICKS(1000));
}

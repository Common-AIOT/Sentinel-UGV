// 센서 ESP32 <-> Jetson USB 직렬 통신 계층.
// 범위: COBS+CRC16 프레이밍, HELLO/HELLO_ACK, ENCODER/IMU/ENVIRONMENT/PROXIMITY_STATE
// 송신(실측값), 300ms 통신 워치독(로컬 액추에이터가 없어 STALE 보고만). MT6701(좌/우
// 구동)·MPU6050 I2C·DHT-11 1-wire·HC-SR04 pulseIn 실측은 sensor_task.cpp가 담당한다.
// 조향 엔코더는 조향 모터가 캐스터 휠로 대체되면서 제거되었다.
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
constexpr uint32_t ENV_TASK_STACK_WORDS = 2048;
constexpr UBaseType_t COMM_TASK_PRIORITY = 2;
constexpr UBaseType_t SENSOR_TASK_PRIORITY = 2;
// §34-8: HC-SR04 pulseIn(최대 30ms)·DHT-11 판독이 100Hz IMU·엔코더 수집을 밀어내지
// 않도록 환경 태스크만 우선순위를 낮춘다.
constexpr UBaseType_t ENV_TASK_PRIORITY = 1;

void setup() {
  sensorHardwareInit();
  sensorSharedStateInit();

  xTaskCreatePinnedToCore(commTaskFn, "comm_task", COMM_TASK_STACK_WORDS, nullptr,
                           COMM_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(sensorTaskFn, "sensor_task", SENSOR_TASK_STACK_WORDS, nullptr,
                           SENSOR_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(envTaskFn, "env_task", ENV_TASK_STACK_WORDS, nullptr,
                           ENV_TASK_PRIORITY, nullptr, 1);
}

void loop() {
  // 모든 동작은 FreeRTOS 태스크(comm_task, sensor_task, env_task)가 담당한다.
  vTaskDelay(pdMS_TO_TICKS(1000));
}

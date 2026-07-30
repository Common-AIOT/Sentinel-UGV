#include "sensor_task.h"

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "board_state.h"

namespace {
constexpr uint32_t SENSOR_TASK_INTERVAL_MS = 20;  // 조향 엔코더/50Hz ENCODER_STATE 송신 주기에 맞춤
}  // namespace

void sensorTaskFn(void* pvParameters) {
  (void)pvParameters;

  for (;;) {
    // TODO(후속 센서 판독 티켓): 아래 값을 MT6701(x3, I2C)·DHT-11(1-wire)·HC-SR04(pulseIn)
    // 실측값으로 교체한다. 지금은 통신 계층이 값 변화를 정상적으로 실어 나르는지 확인하기
    // 위한 placeholder일 뿐이며, 실제 물리량과 무관하다.
    sensorSharedStateUpdate([](SensorSharedState& s) {
      s.driveEncoderTicksLeft += 1;
      s.driveEncoderTicksRight += 1;
      s.driveSpeedLeftMmps = 0;
      s.driveSpeedRightMmps = 0;
      s.measuredSteeringMdeg = 0;
      s.temperatureDeciC = 253;   // 25.3C placeholder
      s.humidityDeciPct = 612;   // 61.2%RH placeholder
      s.environmentStatusFlags = 0;
      s.frontMinDistanceMm = 4000;
      s.validSensorMask = 0x01;
      s.protectiveStop = 0;
      s.lastSensorUpdateMs = millis();
    });

    vTaskDelay(pdMS_TO_TICKS(SENSOR_TASK_INTERVAL_MS));
  }
}

#include "board_state.h"

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

namespace {
SemaphoreHandle_t g_mutex = nullptr;
SensorSharedState g_state;
}  // namespace

void sensorSharedStateInit() {
  if (g_mutex == nullptr) {
    g_mutex = xSemaphoreCreateMutex();
  }
}

SensorSharedState sensorSharedStateSnapshot() {
  SensorSharedState copy;
  if (g_mutex != nullptr && xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
    copy = g_state;
    xSemaphoreGive(g_mutex);
  }
  return copy;
}

void sensorSharedStateUpdate(const std::function<void(SensorSharedState&)>& mutator) {
  if (g_mutex != nullptr && xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
    mutator(g_state);
    xSemaphoreGive(g_mutex);
  }
}

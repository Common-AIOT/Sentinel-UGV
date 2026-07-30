#include "board_state.h"

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

namespace {
SemaphoreHandle_t g_mutex = nullptr;
MotorSharedState g_state;
}  // namespace

void motorSharedStateInit() {
  if (g_mutex == nullptr) {
    g_mutex = xSemaphoreCreateMutex();
  }
}

MotorSharedState motorSharedStateSnapshot() {
  MotorSharedState copy;
  if (g_mutex != nullptr && xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
    copy = g_state;
    xSemaphoreGive(g_mutex);
  }
  return copy;
}

void motorSharedStateUpdate(const std::function<void(MotorSharedState&)>& mutator) {
  if (g_mutex != nullptr && xSemaphoreTake(g_mutex, portMAX_DELAY) == pdTRUE) {
    mutator(g_state);
    xSemaphoreGive(g_mutex);
  }
}

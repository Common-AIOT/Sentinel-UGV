#pragma once

// Serial(921600bps)을 읽어 HELLO/CONFIG를 처리하고, ENCODER_STATE(50Hz)/ENVIRONMENT_STATE(~1Hz)/
// PROXIMITY_STATE(~15Hz)/DIAGNOSTIC(5Hz)를 송신하며 300ms 통신 워치독을 검사하는 FreeRTOS 태스크.
void commTaskFn(void* pvParameters);

#pragma once

// Serial(921600bps)을 읽어 프레임을 파싱·디스패치하고, DRIVE_STATE(50Hz)/DIAGNOSTIC(5Hz)를
// 송신하는 FreeRTOS 태스크. xTaskCreatePinnedToCore의 태스크 함수로 등록한다.
void commTaskFn(void* pvParameters);

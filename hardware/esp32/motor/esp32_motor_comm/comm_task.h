#pragma once

// setup()의 다른 주변장치 초기화보다 먼저 Jetson 전용 UART를 연다.
// 통신 태스크 안에서 Serial.begin()을 호출하면 앞선 초기화가 멈추거나 태스크 생성이
// 실패했을 때 부팅 ROM 이후 아무 바이트도 나오지 않아 원인 판별이 불가능해진다.
void commSerialInit();

// Serial(921600bps)을 읽어 프레임을 파싱·디스패치하고, DRIVE_STATE(50Hz)/DIAGNOSTIC(5Hz)를
// 송신하는 FreeRTOS 태스크. xTaskCreatePinnedToCore의 태스크 함수로 등록한다.
void commTaskFn(void* pvParameters);

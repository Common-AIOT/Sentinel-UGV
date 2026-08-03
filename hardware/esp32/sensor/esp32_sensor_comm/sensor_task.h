#pragma once

// MT6701(좌/우 구동 엔코더, I2C) · DHT-11(1-wire) · HC-SR04(pulseIn) 실측 태스크.
// 조향 엔코더는 조향 모터가 캐스터 휠로 대체되면서 제거되었다.

// I2C/GPIO 핀 초기화. setup()에서 FreeRTOS 태스크 생성 전에 한 번 호출한다.
void sensorHardwareInit();

void sensorTaskFn(void* pvParameters);

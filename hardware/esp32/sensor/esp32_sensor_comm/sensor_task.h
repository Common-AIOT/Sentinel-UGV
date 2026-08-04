#pragma once

// 센서 실측 태스크. §34-8 "저주기 센서 응답 대기가 IMU·엔코더 수집을 막지 않는다"에
// 맞춰 I2C 고주기 계열과 블로킹 저주기 계열을 두 태스크로 나눈다.
//   sensorTaskFn - MT6701(좌/우 구동 엔코더) + MPU6050 IMU, 100Hz, I2C 전용
//   envTaskFn    - HC-SR04(pulseIn 최대 30ms 블로킹) + DHT-11(noInterrupts ~4ms)
// 조향 엔코더는 조향 모터가 캐스터 휠로 대체되면서 제거되었다.

// I2C/GPIO 초기화와 MPU6050 설정. setup()에서 태스크 생성 전에 한 번 호출한다.
void sensorHardwareInit();

void sensorTaskFn(void* pvParameters);
void envTaskFn(void* pvParameters);

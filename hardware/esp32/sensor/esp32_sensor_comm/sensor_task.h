#pragma once

// 센서 실측 태스크. §34-8 "저주기 센서 응답 대기가 IMU·엔코더 수집을 막지 않는다"에
// 맞춰 I2C 고주기 계열과 블로킹 저주기 계열을 두 태스크로 나눈다.
//   sensorTaskFn - MT6701(좌/우 구동 엔코더) + MPU6050 IMU, 100Hz, I2C 전용
//   envTaskFn    - HC-SR04(pulseIn 최대 30ms 블로킹) + DHT-11(noInterrupts ~4ms)
// 엔코더는 후륜 2개뿐이다 - 전륜 조향 서보는 내부 폐루프라 외부 각도 피드백이 없다(§6.3).

// I2C/GPIO 초기화와 MPU6050 설정. setup()에서 태스크 생성 전에 한 번 호출한다.
void sensorHardwareInit();

void sensorTaskFn(void* pvParameters);
void envTaskFn(void* pvParameters);

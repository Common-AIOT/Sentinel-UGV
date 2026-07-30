// 모터·센서 ESP32 공통 fault bit 정의 (docs/03-제어-캘리브레이션.md §34-9)
#pragma once

#include <cstdint>

constexpr uint16_t FAULT_COMM_TIMEOUT_MOTOR = 1u << 0;       // 모터: Jetson<->모터 ESP32 명령 300ms 초과
constexpr uint16_t FAULT_CRC_ERROR_RATE_HIGH = 1u << 1;       // 모터/센서: 해당 채널 직렬 프레임 오류율 초과
constexpr uint16_t FAULT_DRIVE_ENCODER_FAULT = 1u << 2;       // 센서: 좌/우 구동 엔코더 무응답·비정상
constexpr uint16_t FAULT_STEERING_ENCODER_FAULT = 1u << 3;    // 센서: 조향 엔코더(MT6701) 무응답 또는 한계 초과
constexpr uint16_t FAULT_DRIVER_FAULT = 1u << 4;              // 모터: BTS7960 3개 중 1개 이상 fault 입력
constexpr uint16_t FAULT_OVERCURRENT = 1u << 5;               // 모터: 전류 임계값 초과
constexpr uint16_t FAULT_UNDERVOLTAGE = 1u << 6;              // 모터: 구동 전원 저전압
constexpr uint16_t FAULT_ESTOP_ACTIVE = 1u << 7;              // 모터: 물리 E-Stop 입력
constexpr uint16_t FAULT_CONFIG_INVALID = 1u << 8;            // 모터/센서: PPR·PID·한계값 오류
constexpr uint16_t FAULT_INTERNAL_WATCHDOG_RESET = 1u << 9;   // 모터/센서: IWDT/TWDT 재부팅 이력
constexpr uint16_t FAULT_PROXIMITY_SENSOR_FAULT = 1u << 10;   // 센서: 초음파 timeout·비정상값 지속
constexpr uint16_t FAULT_ENVIRONMENT_SENSOR_FAULT = 1u << 11; // 센서: DHT-11 checksum·timeout 지속(DEGRADED 허용)
constexpr uint16_t FAULT_COMM_TIMEOUT_SENSOR = 1u << 12;      // 센서: Jetson<->센서 ESP32 통신 300ms 초과

inline const char* faultBitToName(uint16_t bit) {
  switch (bit) {
    case FAULT_COMM_TIMEOUT_MOTOR: return "COMM_TIMEOUT_MOTOR";
    case FAULT_CRC_ERROR_RATE_HIGH: return "CRC_ERROR_RATE_HIGH";
    case FAULT_DRIVE_ENCODER_FAULT: return "DRIVE_ENCODER_FAULT";
    case FAULT_STEERING_ENCODER_FAULT: return "STEERING_ENCODER_FAULT";
    case FAULT_DRIVER_FAULT: return "DRIVER_FAULT";
    case FAULT_OVERCURRENT: return "OVERCURRENT";
    case FAULT_UNDERVOLTAGE: return "UNDERVOLTAGE";
    case FAULT_ESTOP_ACTIVE: return "ESTOP_ACTIVE";
    case FAULT_CONFIG_INVALID: return "CONFIG_INVALID";
    case FAULT_INTERNAL_WATCHDOG_RESET: return "INTERNAL_WATCHDOG_RESET";
    case FAULT_PROXIMITY_SENSOR_FAULT: return "PROXIMITY_SENSOR_FAULT";
    case FAULT_ENVIRONMENT_SENSOR_FAULT: return "ENVIRONMENT_SENSOR_FAULT";
    case FAULT_COMM_TIMEOUT_SENSOR: return "COMM_TIMEOUT_SENSOR";
    default: return "UNKNOWN_FAULT";
  }
}

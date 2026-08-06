// 모터·센서 ESP32 공통 fault bit 정의 (docs/03-제어-캘리브레이션.md §34-9)
#pragma once

#include <cstdint>

constexpr uint16_t FAULT_COMM_TIMEOUT_MOTOR = 1u << 0;       // 모터: Jetson<->모터 ESP32 명령 300ms 초과
constexpr uint16_t FAULT_CRC_ERROR_RATE_HIGH = 1u << 1;       // 모터/센서: 해당 채널 직렬 프레임 오류율 초과
constexpr uint16_t FAULT_DRIVE_ENCODER_FAULT = 1u << 2;       // 센서: 후륜 좌/우 구동 엔코더 무응답·비정상
constexpr uint16_t FAULT_WHEEL_SPEED_MISMATCH = 1u << 3;      // 센서: 좌·우 구동 속도가 명령 대비 지속적으로 크게 어긋남
constexpr uint16_t FAULT_DRIVER_FAULT = 1u << 4;              // 모터: BTS7960 2개 중 1개 이상 fault 입력
constexpr uint16_t FAULT_OVERCURRENT = 1u << 5;               // 모터: 전류 임계값 초과
constexpr uint16_t FAULT_UNDERVOLTAGE = 1u << 6;              // 모터: 구동 전원 저전압
constexpr uint16_t FAULT_ESTOP_ACTIVE = 1u << 7;              // 모터: 물리 E-Stop 입력
constexpr uint16_t FAULT_CONFIG_INVALID = 1u << 8;            // 모터/센서: PPR·PID·한계값 오류
constexpr uint16_t FAULT_INTERNAL_WATCHDOG_RESET = 1u << 9;   // 모터/센서: IWDT/TWDT 재부팅 이력
constexpr uint16_t FAULT_PROXIMITY_SENSOR_FAULT = 1u << 10;   // 센서: 초음파 timeout·비정상값 지속
constexpr uint16_t FAULT_ENVIRONMENT_SENSOR_FAULT = 1u << 11; // 센서: DHT-11 checksum·timeout 지속(DEGRADED 허용)
constexpr uint16_t FAULT_COMM_TIMEOUT_SENSOR = 1u << 12;      // 센서: Jetson<->센서 ESP32 통신 300ms 초과
constexpr uint16_t FAULT_IMU_SENSOR_FAULT = 1u << 13;          // 센서: IMU bus 오류, 범위 초과, sample timestamp 정지
// 2026-08-06 전륜 서보 조향 전환으로 신설된 두 비트(§34-9 bit 14/15).
constexpr uint16_t FAULT_STEERING_COMMAND_INVALID = 1u << 14;   // 모터: 목표 조향각이 ±δ_max를 넘어 클램프됨 또는 abs(v)<v_min에서 회두 명령 수신
constexpr uint16_t FAULT_STEERING_RESPONSE_MISMATCH = 1u << 15; // 모터/Jetson: 기대 yaw rate와 IMU 실측 yaw rate 지속 불일치(간접 판정, 미구현)

inline const char* faultBitToName(uint16_t bit) {
  switch (bit) {
    case FAULT_COMM_TIMEOUT_MOTOR: return "COMM_TIMEOUT_MOTOR";
    case FAULT_CRC_ERROR_RATE_HIGH: return "CRC_ERROR_RATE_HIGH";
    case FAULT_DRIVE_ENCODER_FAULT: return "DRIVE_ENCODER_FAULT";
    case FAULT_WHEEL_SPEED_MISMATCH: return "WHEEL_SPEED_MISMATCH";
    case FAULT_DRIVER_FAULT: return "DRIVER_FAULT";
    case FAULT_OVERCURRENT: return "OVERCURRENT";
    case FAULT_UNDERVOLTAGE: return "UNDERVOLTAGE";
    case FAULT_ESTOP_ACTIVE: return "ESTOP_ACTIVE";
    case FAULT_CONFIG_INVALID: return "CONFIG_INVALID";
    case FAULT_INTERNAL_WATCHDOG_RESET: return "INTERNAL_WATCHDOG_RESET";
    case FAULT_PROXIMITY_SENSOR_FAULT: return "PROXIMITY_SENSOR_FAULT";
    case FAULT_ENVIRONMENT_SENSOR_FAULT: return "ENVIRONMENT_SENSOR_FAULT";
    case FAULT_COMM_TIMEOUT_SENSOR: return "COMM_TIMEOUT_SENSOR";
    case FAULT_IMU_SENSOR_FAULT: return "IMU_SENSOR_FAULT";
    case FAULT_STEERING_COMMAND_INVALID: return "STEERING_COMMAND_INVALID";
    case FAULT_STEERING_RESPONSE_MISMATCH: return "STEERING_RESPONSE_MISMATCH";
    default: return "UNKNOWN_FAULT";
  }
}

// 센서 ESP32 공유 상태. comm_task/sensor_task 양쪽에서 뮤텍스로 보호된 채 접근한다.
#pragma once

#include <cstdint>
#include <functional>

// docs/03-제어-캘리브레이션.md §34-9 (액추에이터가 없어 모터 보드보다 단순하다)
enum class SensorBoardState : uint8_t {
  BOOT,
  STREAMING,
  DEGRADED,  // 센서 일부 실패(DHT-11 checksum 오류 등) - 실제 판독은 후속 티켓 범위라 이번엔 전이하지 않는다.
  COMM_LOST,
};

struct SensorSharedState {
  // §34-6: 첫 HELLO/ACK 교환 전까지는 BOOT.
  SensorBoardState state = SensorBoardState::BOOT;
  uint16_t faultFlags = 0;

  // placeholder 텔레메트리 값. 실제 MT6701/DHT-11/HC-SR04 판독은 후속 센서 티켓의
  // sensor_task.cpp가 채운다 - 여기서는 통신 계층 검증용 값만 갱신한다.
  int32_t driveEncoderTicksLeft = 0;
  int32_t driveEncoderTicksRight = 0;
  int16_t driveSpeedLeftMmps = 0;
  int16_t driveSpeedRightMmps = 0;
  int16_t measuredSteeringMdeg = 0;

  int16_t temperatureDeciC = 0;
  uint16_t humidityDeciPct = 0;
  uint8_t environmentStatusFlags = 0;

  uint16_t frontMinDistanceMm = 0xFFFF;
  uint8_t validSensorMask = 0;
  uint8_t protectiveStop = 0;

  // sensor_task가 위 값들을 갱신한 시각 - ENCODER/ENVIRONMENT/PROXIMITY_STATE의
  // sample_age_ms 계산에 쓰인다.
  uint32_t lastSensorUpdateMs = 0;

  // Jetson으로부터 유효한 프레임(HELLO/CONFIG 등)을 마지막으로 받은 시각 - 통신 워치독 기준.
  uint32_t lastValidJetsonRxMs = 0;
  bool hasReceivedFromJetson = false;

  // 진단용 누적 카운터 (DIAGNOSTIC 페이로드로 그대로 보고된다)
  uint32_t crcErrorCount = 0;
  uint32_t droppedFrameCount = 0;
  uint32_t staleSequenceCount = 0;
};

void sensorSharedStateInit();
SensorSharedState sensorSharedStateSnapshot();
void sensorSharedStateUpdate(const std::function<void(SensorSharedState&)>& mutator);

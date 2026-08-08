// 센서 ESP32 공유 상태. comm_task/sensor_task 양쪽에서 뮤텍스로 보호된 채 접근한다.
#pragma once

#include <cstdint>
#include <functional>

// docs/03-제어-캘리브레이션.md §34-9 (액추에이터가 없어 모터 보드보다 단순하다)
enum class SensorBoardState : uint8_t {
  BOOT,
  STREAMING,
  DEGRADED,  // 환경(DHT-11)·근접(HC-SR04) 센서 지속 실패 시 sensor_task.cpp가 전이시킨다.
  COMM_LOST,
};

struct SensorSharedState {
  // §34-6: 첫 HELLO/ACK 교환 전까지는 BOOT.
  SensorBoardState state = SensorBoardState::BOOT;
  uint16_t faultFlags = 0;

  // 실측 텔레메트리 값. MT6701(좌/우 구동)·MPU6050·DHT-11·HC-SR04 판독은
  // sensor_task.cpp의 sensorTaskFn/envTaskFn이 채운다.
  int32_t driveEncoderTicksLeft = 0;
  int32_t driveEncoderTicksRight = 0;
  int16_t driveSpeedLeftMmps = 0;
  int16_t driveSpeedRightMmps = 0;
  // 2026-08-06 전륜 조향이 복구됐지만 조향 액추에이터가 서보이고 내부 폐루프라
  // 외부 각도 피드백이 없다 - 이 자리를 채울 센서가 애초에 없으므로 sensor_task.cpp가
  // 항상 0으로 보고한다(§34-5). 엔코더는 후륜 2개 그대로다.
  int16_t measuredSteeringMdeg = 0;

  int16_t temperatureDeciC = 0;
  uint16_t humidityDeciPct = 0;
  uint8_t environmentStatusFlags = 0;

  // MPU6050 차체 IMU. imuSampleTimeUs는 esp_timer_get_time() 기준 monotonic 측정
  // 시각으로, Jetson이 수신 시각 대신 이 값을 ROS timestamp로 변환한다(§34-5).
  // 축은 sensor_task.cpp에서 REP-103(x 전방, y 좌측, z 상방)으로 정렬한 뒤 저장한다.
  uint64_t imuSampleTimeUs = 0;
  float imuGyroXRadps = 0.0f;
  float imuGyroYRadps = 0.0f;
  float imuGyroZRadps = 0.0f;
  float imuAccelXMps2 = 0.0f;
  float imuAccelYMps2 = 0.0f;
  float imuAccelZMps2 = 0.0f;
  int16_t imuTemperatureCentiC = 0;
  // ImuStatusFlag 비트합. 첫 샘플 전에는 0(= VALID 없음)이라 Jetson이 EKF에 넣지 않는다.
  uint16_t imuStatusFlags = 0;

  uint16_t frontMinDistanceMm = 0xFFFF;
  // TBD-HW-010(S15P11A301-324): 후방 HC-SR04. protective_stop에는 아직 반영하지
  // 않는다 - 방향별 임계 실측·안전 체인 통합은 TBD-HW-011·TBD-CAL-001로 미룬다.
  uint16_t rearMinDistanceMm = 0xFFFF;
  uint8_t validSensorMask = 0;
  uint8_t protectiveStop = 0;

  // 각 계열이 마지막으로 갱신된 시각 - 해당 메시지의 sample_age_ms 계산에 쓰인다.
  // 수집 주기가 100Hz(엔코더·IMU) / ~15Hz(초음파) / 0.5Hz(DHT-11)로 크게 달라
  // 하나의 타임스탬프로 합치면 실제보다 신선한 값으로 보고된다.
  uint32_t lastEncoderUpdateMs = 0;
  uint32_t lastProximityUpdateMs = 0;
  uint32_t lastEnvironmentUpdateMs = 0;

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

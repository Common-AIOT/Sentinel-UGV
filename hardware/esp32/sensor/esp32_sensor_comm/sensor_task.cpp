#include "sensor_task.h"

#include <Arduino.h>
#include <Wire.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <math.h>

#include <fault_codes.h>
#include "board_state.h"

// 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
// 추가하지 말 것(README 참고). 상태 확인은 GPIO/LED 또는 Serial2로 한다.

namespace {

// ------------------------------------------------------------
// MT6701 좌/우 구동 엔코더 (단일 I2C 버스, 주소로 구분)
// 조향 엔코더는 조향 모터가 캐스터 휠로 대체되면서 제거되었다.
// ------------------------------------------------------------
constexpr int DRIVE_I2C_SDA_PIN = 21;
constexpr int DRIVE_I2C_SCL_PIN = 22;
constexpr uint32_t I2C_CLOCK_SPEED = 400000;

constexpr uint8_t LEFT_ENCODER_ADDRESS = 0x06;
constexpr uint8_t RIGHT_ENCODER_ADDRESS = 0x46;

constexpr uint8_t REG_ANGLE_HIGH = 0x03;
constexpr uint8_t REG_ANGLE_LOW = 0x04;
constexpr int32_t MT6701_COUNTS_PER_REV = 16384;  // 14비트 절대각

// docs/03-제어-캘리브레이션.md §35-3 실측 전 임시값(encoder/total_mt6701_test 벤치와 동일).
constexpr float LEFT_GEAR_RATIO = 82.0f;
constexpr float RIGHT_GEAR_RATIO = 82.0f;
constexpr float LEFT_DIRECTION_SIGN = 1.0f;
constexpr float RIGHT_DIRECTION_SIGN = 1.0f;
// 실측 전 임시값(§35-3) - 바퀴 유효 지름 확정 후 갱신한다.
constexpr float WHEEL_DIAMETER_MM = 120.0f;
constexpr float WHEEL_CIRCUMFERENCE_MM = WHEEL_DIAMETER_MM * 3.14159265f;

constexpr uint32_t ENCODER_RECONNECT_INTERVAL_MS = 1000;

struct DriveEncoderChannel {
  uint8_t address;
  float gearRatio;
  float directionSign;
  uint16_t previousRaw;
  bool previousValid;
  bool online;
  int32_t accumulatedTicks;
};

DriveEncoderChannel g_leftEncoder{LEFT_ENCODER_ADDRESS, LEFT_GEAR_RATIO, LEFT_DIRECTION_SIGN,
                                   0, false, false, 0};
DriveEncoderChannel g_rightEncoder{RIGHT_ENCODER_ADDRESS, RIGHT_GEAR_RATIO, RIGHT_DIRECTION_SIGN,
                                    0, false, false, 0};

bool readRegister(uint8_t address, uint8_t reg, uint8_t& value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address, static_cast<uint8_t>(1), true) != 1) return false;
  value = Wire.read();
  return true;
}

bool readMT6701Raw(uint8_t address, uint16_t& rawAngle) {
  uint8_t highByte = 0;
  uint8_t lowByte = 0;
  // 데이터시트 지정 순서: 0x03(상위)을 먼저 읽고 0x04(하위)를 읽는다.
  if (!readRegister(address, REG_ANGLE_HIGH, highByte)) return false;
  if (!readRegister(address, REG_ANGLE_LOW, lowByte)) return false;
  rawAngle = (static_cast<uint16_t>(highByte) << 6) | (lowByte >> 2);
  return true;
}

// 0/16384 카운트 경계를 고려한 이동량 계산(부호 있음, 절반 미만 이동 가정).
int32_t countDelta(uint16_t newRaw, uint16_t oldRaw) {
  int32_t delta = static_cast<int32_t>(newRaw) - static_cast<int32_t>(oldRaw);
  if (delta > MT6701_COUNTS_PER_REV / 2) {
    delta -= MT6701_COUNTS_PER_REV;
  } else if (delta < -MT6701_COUNTS_PER_REV / 2) {
    delta += MT6701_COUNTS_PER_REV;
  }
  return delta;
}

// 채널을 한 번 갱신한다. I2C 실패 시 false를 반환하고 상태를 바꾸지 않는다.
bool updateEncoderChannel(DriveEncoderChannel& ch, float dtSeconds, int16_t& speedMmpsOut) {
  uint16_t newRaw = 0;
  if (!readMT6701Raw(ch.address, newRaw)) {
    return false;
  }

  if (!ch.previousValid) {
    ch.previousRaw = newRaw;
    ch.previousValid = true;
    speedMmpsOut = 0;
    return true;
  }

  const int32_t rawDelta = countDelta(newRaw, ch.previousRaw);
  ch.previousRaw = newRaw;

  const int32_t signedDelta = (ch.directionSign < 0.0f) ? -rawDelta : rawDelta;
  ch.accumulatedTicks += signedDelta;

  const float outputRevDelta =
      static_cast<float>(signedDelta) / (MT6701_COUNTS_PER_REV * ch.gearRatio);
  const float distanceDeltaMm = outputRevDelta * WHEEL_CIRCUMFERENCE_MM;
  speedMmpsOut = static_cast<int16_t>(lroundf(distanceDeltaMm / dtSeconds));
  return true;
}

void reconnectEncoderChannel(DriveEncoderChannel& ch) {
  if (ch.online) return;
  Wire.beginTransmission(ch.address);
  if (Wire.endTransmission() == 0) {
    ch.online = true;
    ch.previousValid = false;
  }
}

// ------------------------------------------------------------
// HC-SR04 전방 초음파
// ------------------------------------------------------------
constexpr uint8_t HC_TRIG_PIN = 5;
constexpr uint8_t HC_ECHO_PIN = 36;

constexpr uint32_t ULTRASONIC_INTERVAL_MS = 60;  // ~15-16Hz, §34-5 권장 10~20Hz
constexpr uint32_t ECHO_TIMEOUT_US = 30000;      // 약 5.1m 왕복 상당
constexpr float SPEED_OF_SOUND_CM_PER_US = 0.0343f;
constexpr float MIN_VALID_DISTANCE_CM = 2.0f;
constexpr float MAX_VALID_DISTANCE_CM = 400.0f;

// 실측 전 임시 안전거리(§35 캘리브레이션 대상) - Collision Monitor STOP zone과
// 별개로 센서 ESP32가 로컬 판단만으로 즉시 세우는 최후 방어선이다.
constexpr uint16_t PROXIMITY_STOP_DISTANCE_MM = 300;

// 연속 노이즈성 실패(최소거리 미만 등) 횟수 - 이 이상 지속되면 fault로 본다.
// echo 무응답(timeout)은 "5m 밖에 장애물 없음"으로 취급하며 fault가 아니다.
constexpr uint32_t PROXIMITY_FAULT_STREAK_THRESHOLD = 20;

uint32_t g_ultrasonicFailStreak = 0;

uint32_t readEchoTimeUs() {
  digitalWrite(HC_TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(HC_TRIG_PIN, HIGH);
  delayMicroseconds(12);
  digitalWrite(HC_TRIG_PIN, LOW);
  return pulseIn(HC_ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
}

// true를 반환하면 distanceMmOut이 유효하다(timeout=원거리 clear로 간주).
// false는 최소거리 미만의 노이즈성 반사 등 이번 샘플을 신뢰할 수 없는 경우다.
bool measureUltrasonicMm(uint16_t& distanceMmOut) {
  const uint32_t echoUs = readEchoTimeUs();
  if (echoUs == 0) {
    distanceMmOut = static_cast<uint16_t>(MAX_VALID_DISTANCE_CM * 10.0f);
    return true;
  }

  const float distanceCm = echoUs * SPEED_OF_SOUND_CM_PER_US * 0.5f;
  if (distanceCm < MIN_VALID_DISTANCE_CM) {
    return false;
  }

  const float clampedCm = distanceCm > MAX_VALID_DISTANCE_CM ? MAX_VALID_DISTANCE_CM : distanceCm;
  distanceMmOut = static_cast<uint16_t>(clampedCm * 10.0f);
  return true;
}

// ------------------------------------------------------------
// DHT-11 온습도
// ------------------------------------------------------------
constexpr uint8_t DHT_DATA_PIN = 4;
constexpr uint32_t DHT_INTERVAL_MS = 2000;  // §34-5: 0.5~1Hz 권장, 여유를 두고 2s
constexpr uint32_t DHT_FAULT_STREAK_THRESHOLD = 3;

enum class DhtReadResult : uint8_t { OK, TIMEOUT, CHECKSUM_ERROR };

constexpr uint8_t ENV_STATUS_VALID = 0;
constexpr uint8_t ENV_STATUS_CHECKSUM_ERROR = 1;
constexpr uint8_t ENV_STATUS_TIMEOUT = 2;

uint32_t g_dhtFailStreak = 0;

bool waitForDhtPinState(uint8_t state, uint32_t timeoutUs) {
  const uint32_t startUs = micros();
  while (digitalRead(DHT_DATA_PIN) != state) {
    if (micros() - startUs >= timeoutUs) return false;
  }
  return true;
}

DhtReadResult readDht11(float& temperatureC, float& humidity) {
  uint8_t data[5] = {0, 0, 0, 0, 0};

  // MCU 시작 신호: DATA를 최소 18ms 이상 LOW로 유지한다.
  pinMode(DHT_DATA_PIN, OUTPUT);
  digitalWrite(DHT_DATA_PIN, LOW);
  delay(20);
  digitalWrite(DHT_DATA_PIN, HIGH);
  delayMicroseconds(30);
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  // 약 4ms 전송 구간 동안 비트 타이밍이 흔들리지 않게 한다. 이 구간에는 UART
  // 수신도 멈추므로(§34-5 921600bps 대역폭 대비 HW FIFO 여유 확인 필요), DHT
  // 판독은 2초 주기로만 실행해 comm_task 프레임 유실 확률을 낮춘다.
  noInterrupts();

  bool success = waitForDhtPinState(LOW, 120) && waitForDhtPinState(HIGH, 120) &&
                 waitForDhtPinState(LOW, 120);

  for (uint8_t bitIndex = 0; bitIndex < 40 && success; ++bitIndex) {
    success = waitForDhtPinState(HIGH, 100);
    if (!success) break;

    const uint32_t highStartUs = micros();
    success = waitForDhtPinState(LOW, 120);
    if (!success) break;

    const uint32_t highTimeUs = micros() - highStartUs;
    data[bitIndex / 8] <<= 1;
    if (highTimeUs > 45) data[bitIndex / 8] |= 1;
  }

  interrupts();
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  if (!success) return DhtReadResult::TIMEOUT;

  const uint8_t expectedChecksum =
      static_cast<uint8_t>(data[0] + data[1] + data[2] + data[3]);
  if (data[4] != expectedChecksum) return DhtReadResult::CHECKSUM_ERROR;

  humidity = data[0] + data[1] * 0.1f;
  temperatureC = data[2] + data[3] * 0.1f;
  return DhtReadResult::OK;
}

}  // namespace

void sensorHardwareInit() {
  Wire.begin(DRIVE_I2C_SDA_PIN, DRIVE_I2C_SCL_PIN);
  Wire.setClock(I2C_CLOCK_SPEED);
  Wire.setTimeOut(100);

  pinMode(HC_TRIG_PIN, OUTPUT);
  digitalWrite(HC_TRIG_PIN, LOW);
  pinMode(HC_ECHO_PIN, INPUT);
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  // DHT-11 전원 안정화 대기(데이터시트 권장).
  delay(1000);

  Wire.beginTransmission(LEFT_ENCODER_ADDRESS);
  g_leftEncoder.online = (Wire.endTransmission() == 0);
  Wire.beginTransmission(RIGHT_ENCODER_ADDRESS);
  g_rightEncoder.online = (Wire.endTransmission() == 0);
}

void sensorTaskFn(void* pvParameters) {
  (void)pvParameters;

  constexpr uint32_t SENSOR_TASK_INTERVAL_MS = 20;  // 50Hz ENCODER_STATE 송신 주기에 맞춤
  constexpr float SENSOR_TASK_INTERVAL_S = SENSOR_TASK_INTERVAL_MS / 1000.0f;

  uint32_t lastReconnectMs = 0;
  uint32_t lastUltrasonicMs = 0;
  uint32_t lastDhtMs = 0;

  for (;;) {
    const uint32_t now = millis();

    int16_t leftSpeedMmps = 0;
    int16_t rightSpeedMmps = 0;

    if (g_leftEncoder.online &&
        !updateEncoderChannel(g_leftEncoder, SENSOR_TASK_INTERVAL_S, leftSpeedMmps)) {
      g_leftEncoder.online = false;
    }
    if (g_rightEncoder.online &&
        !updateEncoderChannel(g_rightEncoder, SENSOR_TASK_INTERVAL_S, rightSpeedMmps)) {
      g_rightEncoder.online = false;
    }

    if (now - lastReconnectMs >= ENCODER_RECONNECT_INTERVAL_MS) {
      lastReconnectMs = now;
      reconnectEncoderChannel(g_leftEncoder);
      reconnectEncoderChannel(g_rightEncoder);
    }

    bool ultrasonicUpdated = false;
    bool ultrasonicValid = false;
    uint16_t distanceMm = 0;
    if (now - lastUltrasonicMs >= ULTRASONIC_INTERVAL_MS) {
      lastUltrasonicMs = now;
      ultrasonicUpdated = true;
      ultrasonicValid = measureUltrasonicMm(distanceMm);
      g_ultrasonicFailStreak = ultrasonicValid ? 0 : (g_ultrasonicFailStreak + 1);
    }

    bool dhtUpdated = false;
    DhtReadResult dhtResult = DhtReadResult::OK;
    float temperatureC = 0.0f;
    float humidity = 0.0f;
    if (now - lastDhtMs >= DHT_INTERVAL_MS) {
      lastDhtMs = now;
      dhtUpdated = true;
      dhtResult = readDht11(temperatureC, humidity);
      g_dhtFailStreak = (dhtResult == DhtReadResult::OK) ? 0 : (g_dhtFailStreak + 1);
    }

    const bool driveEncoderFault = !g_leftEncoder.online || !g_rightEncoder.online;

    sensorSharedStateUpdate([&](SensorSharedState& s) {
      s.driveEncoderTicksLeft = g_leftEncoder.accumulatedTicks;
      s.driveEncoderTicksRight = g_rightEncoder.accumulatedTicks;
      s.driveSpeedLeftMmps = g_leftEncoder.online ? leftSpeedMmps : 0;
      s.driveSpeedRightMmps = g_rightEncoder.online ? rightSpeedMmps : 0;
      // 조향 모터·조향 엔코더가 캐스터 휠로 대체되어 더 이상 존재하지 않는다.
      s.measuredSteeringMdeg = 0;

      if (driveEncoderFault) {
        s.faultFlags |= FAULT_DRIVE_ENCODER_FAULT;
      } else {
        s.faultFlags &= ~FAULT_DRIVE_ENCODER_FAULT;
      }

      if (ultrasonicUpdated) {
        if (ultrasonicValid) {
          s.frontMinDistanceMm = distanceMm;
          s.validSensorMask |= 0x01;
          s.protectiveStop = (distanceMm <= PROXIMITY_STOP_DISTANCE_MM) ? 1 : 0;
        } else {
          s.validSensorMask &= ~0x01;
        }

        if (g_ultrasonicFailStreak >= PROXIMITY_FAULT_STREAK_THRESHOLD) {
          s.faultFlags |= FAULT_PROXIMITY_SENSOR_FAULT;
        } else if (ultrasonicValid) {
          s.faultFlags &= ~FAULT_PROXIMITY_SENSOR_FAULT;
        }
      }

      if (dhtUpdated) {
        if (dhtResult == DhtReadResult::OK) {
          s.temperatureDeciC = static_cast<int16_t>(lroundf(temperatureC * 10.0f));
          s.humidityDeciPct = static_cast<uint16_t>(lroundf(humidity * 10.0f));
          s.environmentStatusFlags = ENV_STATUS_VALID;
        } else {
          s.environmentStatusFlags = (dhtResult == DhtReadResult::CHECKSUM_ERROR)
                                          ? ENV_STATUS_CHECKSUM_ERROR
                                          : ENV_STATUS_TIMEOUT;
        }

        if (g_dhtFailStreak >= DHT_FAULT_STREAK_THRESHOLD) {
          s.faultFlags |= FAULT_ENVIRONMENT_SENSOR_FAULT;
        } else if (dhtResult == DhtReadResult::OK) {
          s.faultFlags &= ~FAULT_ENVIRONMENT_SENSOR_FAULT;
        }
      }

      // DEGRADED는 이 태스크가 소유한다(환경/근접 fault 기준). BOOT/COMM_LOST
      // 전이는 comm_task의 통신 워치독이 소유하므로 여기서 건드리지 않는다.
      const bool degraded =
          (s.faultFlags & (FAULT_ENVIRONMENT_SENSOR_FAULT | FAULT_PROXIMITY_SENSOR_FAULT)) != 0;
      if (s.state == SensorBoardState::STREAMING && degraded) {
        s.state = SensorBoardState::DEGRADED;
      } else if (s.state == SensorBoardState::DEGRADED && !degraded) {
        s.state = SensorBoardState::STREAMING;
      }

      s.lastSensorUpdateMs = now;
    });

    vTaskDelay(pdMS_TO_TICKS(SENSOR_TASK_INTERVAL_MS));
  }
}

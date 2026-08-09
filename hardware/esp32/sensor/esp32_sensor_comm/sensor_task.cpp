#include "sensor_task.h"

#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <math.h>
#include <string.h>

#include <fault_codes.h>
#include <message_ids.h>
#include "board_state.h"

// 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
// 추가하지 말 것(README 참고). 상태 확인은 GPIO/LED 또는 Serial2로 한다.

namespace {

// ------------------------------------------------------------
// 공용 I2C 헬퍼 (MT6701 · MPU6050이 GPIO21/22 버스를 공유한다)
// ------------------------------------------------------------
bool readRegister(uint8_t address, uint8_t reg, uint8_t& value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address, static_cast<uint8_t>(1), true) != 1) return false;
  value = Wire.read();
  return true;
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t* out, uint8_t count) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address, count, true) != count) return false;
  for (uint8_t i = 0; i < count; ++i) out[i] = Wire.read();
  return true;
}

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

// ------------------------------------------------------------
// MT6701 후륜 좌/우 구동 엔코더 (단일 I2C 버스, 주소로 구분)
// 조향각 엔코더는 없다 - 전륜 조향 서보가 내부 폐루프라 외부 피드백이 없다(§6.3).
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

constexpr uint32_t I2C_RECONNECT_INTERVAL_MS = 1000;

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
// MPU6050 차체 IMU (같은 I2C 버스, AD0=GND → 0x68)
// MT6701 0x06/0x46과 주소가 겹치지 않아 GPIO21/22를 그대로 공유한다.
// ------------------------------------------------------------
constexpr uint8_t MPU6050_ADDRESS = 0x68;

constexpr uint8_t MPU_REG_SMPLRT_DIV = 0x19;
constexpr uint8_t MPU_REG_CONFIG = 0x1A;
constexpr uint8_t MPU_REG_GYRO_CONFIG = 0x1B;
constexpr uint8_t MPU_REG_ACCEL_CONFIG = 0x1C;
constexpr uint8_t MPU_REG_ACCEL_XOUT_H = 0x3B;  // accel(6) + temp(2) + gyro(6) 연속 14바이트
constexpr uint8_t MPU_REG_PWR_MGMT_1 = 0x6B;
constexpr uint8_t MPU_REG_WHO_AM_I = 0x75;

constexpr uint8_t MPU_PWR_DEVICE_RESET = 0x80;
constexpr uint8_t MPU_PWR_CLOCK_PLL_X = 0x01;  // 내부 8MHz RC보다 드리프트가 작다
constexpr uint8_t MPU_DLPF_CFG_44HZ = 0x03;    // accel 44Hz / gyro 42Hz, gyro 출력 1kHz
constexpr uint8_t MPU_SMPLRT_DIV_100HZ = 9;    // 1000Hz / (1 + 9)
constexpr uint8_t MPU_GYRO_FS_500DPS = 0x08;   // FS_SEL=1
constexpr uint8_t MPU_ACCEL_FS_4G = 0x08;      // AFS_SEL=1

constexpr float GYRO_LSB_PER_DPS = 65.5f;     // ±500dps
constexpr float ACCEL_LSB_PER_G = 8192.0f;    // ±4g
// Arduino.h가 DEG_TO_RAD를 double 매크로로 정의하고 있어(comm_task.cpp의 min() 주석과
// 같은 문제) 이름을 겹치지 않게 두고 float으로 계산한다.
constexpr float IMU_DEG_TO_RAD = 3.14159265f / 180.0f;
constexpr float STANDARD_GRAVITY_MPS2 = 9.80665f;

// 장착 방향 보정: REP-103 축[0]=x 전방, [1]=y 좌측, [2]=z 상방 각각이 MPU6050 칩
// 축(0=X, 1=Y, 2=Z) 중 무엇에 대응하는지와 부호를 적는다. 기판 실크스크린 축이
// 그대로 전방/좌측/상방이면 아래 항등 설정을 유지한다. 예를 들어 기판을 90° 돌려
// 칩 +Y가 전방을 보게 달았다면 SOURCE={1, 0, 2}, SIGN={1, -1, 1}이 된다.
constexpr uint8_t IMU_AXIS_SOURCE[3] = {0, 1, 2};
constexpr float IMU_AXIS_SIGN[3] = {1.0f, 1.0f, 1.0f};

// 자이로 바이어스 수집: 정지 상태 2초. 이 동안 status_flags에 CALIBRATING을 세워
// Jetson이 EKF에 넣지 않게 한다(§34-5).
constexpr uint32_t IMU_CALIBRATION_SAMPLES = 200;
// 수집 중 이 값을 넘는 각속도가 보이면 차체가 움직이는 것으로 보고 처음부터 다시 모은다.
constexpr float IMU_CALIBRATION_MOTION_LIMIT_RADPS = 20.0f * IMU_DEG_TO_RAD;

// 연속 I2C 실패 횟수 - 이 이상이면 FAULT_IMU_SENSOR_FAULT로 본다(100ms 상당).
constexpr uint32_t IMU_FAULT_STREAK_THRESHOLD = 10;
// 14바이트 원시값이 완전히 동일한 샘플이 이만큼 이어지면 "sample timestamp 정지"로
// 판단한다(§34-9 row 13). 실제 동작 중에는 accel 노이즈 때문에 나올 수 없는 값이다.
constexpr uint32_t IMU_STUCK_STREAK_THRESHOLD = 100;

struct ImuSample {
  uint64_t sampleTimeUs;
  float gyroRadps[3];   // REP-103 정렬 + 바이어스 보정 완료
  float accelMps2[3];   // REP-103 정렬
  int16_t temperatureCentiC;
  uint16_t statusFlags;
};

struct ImuChannel {
  bool online;
  bool calibrated;
  uint32_t calibrationCount;
  float gyroBiasRadps[3];
  float gyroBiasAccum[3];
  uint32_t failStreak;
  uint32_t stuckStreak;
  bool previousRawValid;
  uint8_t previousRaw[14];
};

ImuChannel g_imu{};

// 초기화는 리셋 대기 100ms를 두 단계로 쪼개 진행한다. 100Hz 태스크 안에서
// delay(110)을 돌면 그 사이 MT6701 델타 계수가 반 바퀴를 넘어 aliasing될 수 있어,
// 재접속 경로에서는 블로킹하지 않는다(재접속 주기 1s가 리셋 대기를 대신한다).
enum class ImuInitPhase : uint8_t {
  IDLE,            // 다음 시도는 probe + DEVICE_RESET부터
  AWAITING_RESET,  // DEVICE_RESET을 보냈고 설정 쓰기만 남았다
};

ImuInitPhase g_imuInitPhase = ImuInitPhase::IDLE;

// WHO_AM_I로 존재를 확인하고 DEVICE_RESET을 보낸다.
bool imuProbeAndReset() {
  uint8_t whoAmI = 0;
  if (!readRegister(MPU6050_ADDRESS, MPU_REG_WHO_AM_I, whoAmI)) return false;
  // 정품은 0x68, 호환 칩(MPU6052/클론)은 0x70·0x72·0x98 등을 돌려준다. 레지스터
  // 맵이 같으므로 응답이 오기만 하면 진행하고, 0x00/0xFF만 배선 불량으로 거른다.
  if (whoAmI == 0x00 || whoAmI == 0xFF) return false;
  return writeRegister(MPU6050_ADDRESS, MPU_REG_PWR_MGMT_1, MPU_PWR_DEVICE_RESET);
}

// DEVICE_RESET 후 최소 100ms가 지난 다음에 호출해야 한다.
bool imuApplyConfig() {
  if (!writeRegister(MPU6050_ADDRESS, MPU_REG_PWR_MGMT_1, MPU_PWR_CLOCK_PLL_X)) return false;
  if (!writeRegister(MPU6050_ADDRESS, MPU_REG_CONFIG, MPU_DLPF_CFG_44HZ)) return false;
  if (!writeRegister(MPU6050_ADDRESS, MPU_REG_SMPLRT_DIV, MPU_SMPLRT_DIV_100HZ)) return false;
  if (!writeRegister(MPU6050_ADDRESS, MPU_REG_GYRO_CONFIG, MPU_GYRO_FS_500DPS)) return false;
  if (!writeRegister(MPU6050_ADDRESS, MPU_REG_ACCEL_CONFIG, MPU_ACCEL_FS_4G)) return false;
  // PLL 안정화 10ms는 첫 판독까지의 태스크 주기(10ms 이상)로 충족된다.
  return true;
}

// setup()에서만 쓰는 블로킹 버전 - 태스크가 아직 없어 delay를 써도 안전하다.
bool configureImuBlocking() {
  if (!imuProbeAndReset()) return false;
  delay(100);  // 데이터시트 권장 리셋 대기
  return imuApplyConfig();
}

void resetImuCalibration(ImuChannel& imu) {
  imu.calibrated = false;
  imu.calibrationCount = 0;
  for (int i = 0; i < 3; ++i) {
    imu.gyroBiasAccum[i] = 0.0f;
    imu.gyroBiasRadps[i] = 0.0f;
  }
  imu.previousRawValid = false;
  imu.stuckStreak = 0;
}

int16_t beI16(const uint8_t* buf) {
  return static_cast<int16_t>((static_cast<uint16_t>(buf[0]) << 8) | buf[1]);
}

// 칩 축 3개를 REP-103 축으로 재배치한다(부호 있는 축 치환이므로 바이어스에도 그대로 쓴다).
void remapToRep103(const float chipAxes[3], float out[3]) {
  for (int i = 0; i < 3; ++i) {
    out[i] = IMU_AXIS_SIGN[i] * chipAxes[IMU_AXIS_SOURCE[i]];
  }
}

// 한 샘플을 읽어 sampleOut을 채운다. I2C가 죽었으면 false(호출자가 online을 내린다).
bool updateImu(ImuChannel& imu, ImuSample& sampleOut) {
  // accel(6) + temp(2) + gyro(6)을 한 번의 버스트로 읽어 축 간 시각 차를 없앤다.
  uint8_t raw[14];
  if (!readRegisters(MPU6050_ADDRESS, MPU_REG_ACCEL_XOUT_H, raw,
                     static_cast<uint8_t>(sizeof(raw)))) {
    return false;
  }
  // 측정 시각은 판독 직후에 찍는다. millis()는 32비트라 49.7일에 감기므로
  // u64 sample_time_us에는 esp_timer_get_time()을 쓴다.
  sampleOut.sampleTimeUs = static_cast<uint64_t>(esp_timer_get_time());

  const bool identical =
      imu.previousRawValid && memcmp(raw, imu.previousRaw, sizeof(raw)) == 0;
  imu.stuckStreak = identical ? (imu.stuckStreak + 1) : 0;
  memcpy(imu.previousRaw, raw, sizeof(raw));
  imu.previousRawValid = true;

  const int16_t accelRaw[3] = {beI16(raw + 0), beI16(raw + 2), beI16(raw + 4)};
  const int16_t tempRaw = beI16(raw + 6);
  const int16_t gyroRaw[3] = {beI16(raw + 8), beI16(raw + 10), beI16(raw + 12)};

  bool saturated = false;
  for (int i = 0; i < 3; ++i) {
    if (accelRaw[i] <= -32767 || accelRaw[i] >= 32767) saturated = true;
    if (gyroRaw[i] <= -32767 || gyroRaw[i] >= 32767) saturated = true;
  }

  float chipGyro[3];
  float chipAccel[3];
  for (int i = 0; i < 3; ++i) {
    chipGyro[i] = (gyroRaw[i] / GYRO_LSB_PER_DPS) * IMU_DEG_TO_RAD;
    chipAccel[i] = (accelRaw[i] / ACCEL_LSB_PER_G) * STANDARD_GRAVITY_MPS2;
  }
  remapToRep103(chipGyro, sampleOut.gyroRadps);
  remapToRep103(chipAccel, sampleOut.accelMps2);

  // 데이터시트 6.4: T[°C] = raw/340 + 36.53.
  sampleOut.temperatureCentiC = static_cast<int16_t>(lroundf(tempRaw / 3.4f + 3653.0f));

  if (!imu.calibrated) {
    bool moving = false;
    for (int i = 0; i < 3; ++i) {
      if (fabsf(sampleOut.gyroRadps[i]) > IMU_CALIBRATION_MOTION_LIMIT_RADPS) moving = true;
    }
    if (moving) {
      // 차체가 움직이는 동안 모은 값은 바이어스가 아니므로 버리고 다시 시작한다.
      imu.calibrationCount = 0;
      for (int i = 0; i < 3; ++i) imu.gyroBiasAccum[i] = 0.0f;
    } else {
      for (int i = 0; i < 3; ++i) imu.gyroBiasAccum[i] += sampleOut.gyroRadps[i];
      imu.calibrationCount++;
      if (imu.calibrationCount >= IMU_CALIBRATION_SAMPLES) {
        for (int i = 0; i < 3; ++i) {
          imu.gyroBiasRadps[i] = imu.gyroBiasAccum[i] / static_cast<float>(imu.calibrationCount);
        }
        imu.calibrated = true;
      }
    }
  }

  if (imu.calibrated) {
    for (int i = 0; i < 3; ++i) sampleOut.gyroRadps[i] -= imu.gyroBiasRadps[i];
  }

  const bool stuck = imu.stuckStreak >= IMU_STUCK_STREAK_THRESHOLD;
  uint16_t flags = 0;
  if (!imu.calibrated) flags |= IMU_STATUS_CALIBRATING;
  if (saturated) flags |= IMU_STATUS_RANGE_ERROR;
  if (stuck) flags |= IMU_STATUS_BUS_ERROR;
  if (flags == 0) flags = IMU_STATUS_VALID;
  sampleOut.statusFlags = flags;
  return true;
}

// ------------------------------------------------------------
// HC-SR04 전방·후방 초음파(TBD-HW-010, S15P11A301-324)
// 같은 센서 ESP32에 독립 TRIG/ECHO 핀 쌍으로 붙는다. 두 센서를 동시에
// trigger하면 서로의 echo를 오독하므로(02장 6.5·21.3) envTaskFn에서 전방을
// 완전히 측정(pulseIn 종료 또는 timeout)한 뒤에만 후방을 trigger한다.
//
// **2026-08-08 실차 배선 점검(S15P11A301-324): GPIO5/GPIO36에 물린 HC-SR04는
// 실제로는 후방에 달려 있었다.** 센서 위치를 다시 바꾸기 어려워 아래 전방/후방
// 핀 배정을 맞바꿨다 - 핀 번호 자체(5/36, 18/39)는 바뀌지 않았다.
// ------------------------------------------------------------
// 전방 핀은 실제 도통 시험 전 임시값이다(부록 J는 TBD-HW-010 확정 전까지 TBD로
// 남긴다) - 앞바퀴 조향 서보(모터 ESP32)나 이 보드의 다른 핀과 겹치지 않는
// 범용 GPIO 쌍을 골랐다. ECHO는 5V 신호라 후방과 마찬가지로 레벨 변환이 필요하다.
constexpr uint8_t HC_TRIG_PIN = 18;
constexpr uint8_t HC_ECHO_PIN = 39;

// 후방 핀은 실배선 점검으로 확인된 값이다(위 2026-08-08 항목 참고).
constexpr uint8_t HC_REAR_TRIG_PIN = 5;
constexpr uint8_t HC_REAR_ECHO_PIN = 36;

constexpr uint32_t ULTRASONIC_INTERVAL_MS = 60;  // ~15-16Hz, §34-5 권장 10~20Hz(전방·후방 공통)
constexpr uint32_t ECHO_TIMEOUT_US = 30000;      // 약 5.1m 왕복 상당
constexpr float SPEED_OF_SOUND_CM_PER_US = 0.0343f;
constexpr float MIN_VALID_DISTANCE_CM = 2.0f;
constexpr float MAX_VALID_DISTANCE_CM = 400.0f;

// 실측 전 임시 안전거리(§35 캘리브레이션 대상) - Collision Monitor STOP zone과
// 별개로 센서 ESP32가 로컬 판단만으로 즉시 세우는 최후 방어선이다. 전방
// 전용이다 - 후방은 장착 각도·높이가 달라 이 값을 그대로 쓸 수 없고
// (TBD-CAL-001), protective_stop 반영 여부 자체가 안전 체인 통합 결정
// 사항이라(TBD-HW-011) 아직 반영하지 않는다.
constexpr uint16_t PROXIMITY_STOP_DISTANCE_MM = 100;

// **전방 보호정지 발동을 끈다** (2026-08-09, S15P11A301-353).
//
// 전방 HC-SR04 가 빈 공간에서 2.6~5.5cm 오측을 간헐적으로 내(15초 218표본 중
// 9회, 커넥터 분리 시 0회 — 센서·배선 원인 확정) 위 임계에 걸릴 때마다
// STOP_COMMAND 가 중계돼 주행이 끊겼다. 장애물 정지는 라이다 경로
// (collision_monitor 정지 다각형, 전방 0.40m)가 담당하고 있어 초음파 발동
// 없이도 시연 요구를 충족한다는 판단으로 **센서는 달아두되 정지 권한만 뺐다**.
//
// **끄는 지점이 여기여야 하는 이유**: 젯슨 쪽 중계(relay_protective_stop)만
// 끄면 safety_gate 가 같은 토픽을 독립적으로 보고 여전히 막고, 발행을 멈추면
// PROXIMITY_STALE 로 막는다(침묵도 차단 사유). 측정·발행은 유지한 채 발동
// 판정만 꺼야 어느 층도 안 막힌다 — 후방 초음파가 이미 이 지위다(TBD-HW-011).
//
// **받아들인 리스크**: 라이다 평면(z=0.50) 아래의 낮은 장애물은 이제 아무도
// 못 본다. 사람 다리·의자는 라이다에 잡히므로 교실 시연 기준 수용. 되살리려면
// 이 플래그를 true 로 하고 353 완료 기준(빈 공간 15초 오측 0회)을 먼저 통과할 것.
constexpr bool PROXIMITY_STOP_ENABLED = false;

// 연속 노이즈성 실패(최소거리 미만 등) 횟수 - 이 이상 지속되면 fault로 본다.
// echo 무응답(timeout)은 "5m 밖에 장애물 없음"으로 취급하며 fault가 아니다.
constexpr uint32_t PROXIMITY_FAULT_STREAK_THRESHOLD = 20;

// validSensorMask 비트(esp32_sensor_bridge_node.py와 값을 맞출 것).
constexpr uint8_t PROXIMITY_FRONT_VALID_BIT = 0x01;
constexpr uint8_t PROXIMITY_REAR_VALID_BIT = 0x02;

uint32_t g_frontUltrasonicFailStreak = 0;
uint32_t g_rearUltrasonicFailStreak = 0;

uint32_t readEchoTimeUs(uint8_t trigPin, uint8_t echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(3);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(12);
  digitalWrite(trigPin, LOW);
  return pulseIn(echoPin, HIGH, ECHO_TIMEOUT_US);
}

// true를 반환하면 distanceMmOut이 유효하다(timeout=원거리 clear로 간주).
// false는 최소거리 미만의 노이즈성 반사 등 이번 샘플을 신뢰할 수 없는 경우다.
bool measureUltrasonicMm(uint8_t trigPin, uint8_t echoPin, uint16_t& distanceMmOut) {
  const uint32_t echoUs = readEchoTimeUs(trigPin, echoPin);
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
  pinMode(HC_REAR_TRIG_PIN, OUTPUT);
  digitalWrite(HC_REAR_TRIG_PIN, LOW);
  pinMode(HC_REAR_ECHO_PIN, INPUT);
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  // DHT-11 전원 안정화 대기(데이터시트 권장). MPU6050 시동 시간도 함께 확보된다.
  delay(1000);

  Wire.beginTransmission(LEFT_ENCODER_ADDRESS);
  g_leftEncoder.online = (Wire.endTransmission() == 0);
  Wire.beginTransmission(RIGHT_ENCODER_ADDRESS);
  g_rightEncoder.online = (Wire.endTransmission() == 0);

  // 바이어스 수집은 sensorTaskFn에서 CALIBRATING을 보고하며 진행한다(여기서 블로킹하지 않는다).
  resetImuCalibration(g_imu);
  g_imu.failStreak = 0;
  g_imu.online = configureImuBlocking();
  g_imuInitPhase = ImuInitPhase::IDLE;
}

void sensorTaskFn(void* pvParameters) {
  (void)pvParameters;

  // §34-8: IMU I2C 읽기 100Hz. 엔코더도 같은 버스라 같은 주기로 함께 읽는다
  // (ENCODER_STATE 송신은 comm_task가 50Hz로 계속 내보낸다).
  //
  // **주기를 절대 시각으로 고정한다** (S15P11A301-339). 종전의 `vTaskDelay` 는
  // 상대 지연이라 실제 주기가 10ms + 작업시간이 됐다. 같은 루프에서 MT6701 두 개와
  // MPU6050 을 I2C 로 읽으므로 실측 약 34ms 였고, 0.15 m/s 에서 샘플당 모터 0.53
  // 회전이라 MT6701 의 반회전 모호성(`countDelta`)을 넘겼다. 계측 주행에서 전진
  // +2.0m 가 -0.245m 로 **부호까지 반전**되어 보고된 것이 그 증거다.
  constexpr uint32_t SENSOR_TASK_INTERVAL_MS = 10;

  uint32_t lastReconnectMs = 0;
  // dt 를 실측한다. **명목값을 쓰면 안 된다** (S15P11A301-339). 종전에는
  // `SENSOR_TASK_INTERVAL_MS / 1000.0f` 를 그대로 넘겨서, 34ms 동안 움직인 거리를
  // 10ms 로 나눴다 — 보고 속도가 약 3.4배 부풀려졌다. 위 절대 주기 고정과 **다른
  // 결함이다**: 감김은 countDelta 의 문제이고 이것은 나눗셈의 문제라, 하나만
  // 고치면 다른 하나가 남는다. 주기가 밀리는 순간(I2C 재연결 등)에도 속도가
  // 틀리지 않으려면 실측이어야 한다.
  uint32_t lastUs = micros();
  TickType_t xLastWakeTime = xTaskGetTickCount();

  for (;;) {
    const uint32_t now = millis();
    const uint32_t nowUs = micros();
    // micros() 는 32비트라 약 71.6분에 감긴다. 부호 없는 뺄셈이라 감기는 순간에도
    // 차이가 맞는다 — nowUs 나 lastUs 를 signed 로 바꾸면 그 성질이 깨진다.
    const float dtSeconds = (nowUs - lastUs) / 1e6f;
    lastUs = nowUs;

    int16_t leftSpeedMmps = 0;
    int16_t rightSpeedMmps = 0;

    if (g_leftEncoder.online &&
        !updateEncoderChannel(g_leftEncoder, dtSeconds, leftSpeedMmps)) {
      g_leftEncoder.online = false;
    }
    if (g_rightEncoder.online &&
        !updateEncoderChannel(g_rightEncoder, dtSeconds, rightSpeedMmps)) {
      g_rightEncoder.online = false;
    }

    ImuSample imuSample{};
    bool imuUpdated = false;
    if (g_imu.online) {
      imuUpdated = updateImu(g_imu, imuSample);
      if (imuUpdated) {
        g_imu.failStreak = 0;
      } else {
        g_imu.online = false;
        g_imu.failStreak++;
        g_imuInitPhase = ImuInitPhase::IDLE;
      }
    } else {
      g_imu.failStreak++;
    }

    if (now - lastReconnectMs >= I2C_RECONNECT_INTERVAL_MS) {
      lastReconnectMs = now;
      reconnectEncoderChannel(g_leftEncoder);
      reconnectEncoderChannel(g_rightEncoder);

      if (!g_imu.online) {
        if (g_imuInitPhase == ImuInitPhase::AWAITING_RESET) {
          // 이전 주기에 DEVICE_RESET을 보냈으므로 100ms 리셋 대기는 이미 지났다.
          if (imuApplyConfig()) {
            // 재설정 후의 바이어스는 신뢰할 수 없으므로 다시 수집한다.
            resetImuCalibration(g_imu);
            g_imu.online = true;
            g_imu.failStreak = 0;
          }
          g_imuInitPhase = ImuInitPhase::IDLE;
        } else if (imuProbeAndReset()) {
          g_imuInitPhase = ImuInitPhase::AWAITING_RESET;
        }
      }
    }

    const bool driveEncoderFault = !g_leftEncoder.online || !g_rightEncoder.online;
    const bool imuFault = g_imu.failStreak >= IMU_FAULT_STREAK_THRESHOLD ||
                          g_imu.stuckStreak >= IMU_STUCK_STREAK_THRESHOLD;

    sensorSharedStateUpdate([&](SensorSharedState& s) {
      s.driveEncoderTicksLeft = g_leftEncoder.accumulatedTicks;
      s.driveEncoderTicksRight = g_rightEncoder.accumulatedTicks;
      s.driveSpeedLeftMmps = g_leftEncoder.online ? leftSpeedMmps : 0;
      s.driveSpeedRightMmps = g_rightEncoder.online ? rightSpeedMmps : 0;
      // 조향각을 재는 센서가 없다(서보 내부 폐루프, §34-5). 자리만 유지한다.
      s.measuredSteeringMdeg = 0;

      if (driveEncoderFault) {
        s.faultFlags |= FAULT_DRIVE_ENCODER_FAULT;
      } else {
        s.faultFlags &= ~FAULT_DRIVE_ENCODER_FAULT;
      }

      if (imuUpdated) {
        s.imuSampleTimeUs = imuSample.sampleTimeUs;
        s.imuGyroXRadps = imuSample.gyroRadps[0];
        s.imuGyroYRadps = imuSample.gyroRadps[1];
        s.imuGyroZRadps = imuSample.gyroRadps[2];
        s.imuAccelXMps2 = imuSample.accelMps2[0];
        s.imuAccelYMps2 = imuSample.accelMps2[1];
        s.imuAccelZMps2 = imuSample.accelMps2[2];
        s.imuTemperatureCentiC = imuSample.temperatureCentiC;
        s.imuStatusFlags = imuSample.statusFlags;
      } else {
        // 판독 실패 시 마지막 값을 VALID로 다시 내보내지 않는다. 값은 그대로 두고
        // BUS_ERROR만 세워 Jetson이 EKF 입력에서 제외하게 한다.
        s.imuStatusFlags = IMU_STATUS_BUS_ERROR;
      }

      if (imuFault) {
        s.faultFlags |= FAULT_IMU_SENSOR_FAULT;
      } else if (imuUpdated) {
        s.faultFlags &= ~FAULT_IMU_SENSOR_FAULT;
      }

      // 엔코더·IMU는 오도메트리 입력이라 로컬 DEGRADED 전이 대상이 아니다. fault만
      // 보고하고 AUTO 중단 여부는 Jetson이 판단한다(§34-9). DEGRADED는 환경/근접
      // 기준으로 envTaskFn이 소유한다.
      s.lastEncoderUpdateMs = now;
    });

    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(SENSOR_TASK_INTERVAL_MS));
  }
}

void envTaskFn(void* pvParameters) {
  (void)pvParameters;

  // HC-SR04 pulseIn은 최대 30ms, DHT-11은 ~24ms(그중 4ms는 noInterrupts) 블로킹한다.
  // §34-8에 따라 IMU·엔코더 수집을 막지 않도록 낮은 우선순위의 별도 태스크로 둔다.
  constexpr uint32_t ENV_TASK_INTERVAL_MS = 10;

  uint32_t lastUltrasonicMs = 0;
  uint32_t lastDhtMs = 0;

  for (;;) {
    const uint32_t now = millis();

    bool ultrasonicUpdated = false;
    bool frontUltrasonicValid = false;
    bool rearUltrasonicValid = false;
    uint16_t frontDistanceMm = 0;
    uint16_t rearDistanceMm = 0;
    if (now - lastUltrasonicMs >= ULTRASONIC_INTERVAL_MS) {
      lastUltrasonicMs = now;
      ultrasonicUpdated = true;
      // 전방을 완전히 측정(에코 종료 또는 30ms timeout)한 뒤에만 후방을
      // trigger한다 - 동시 측정은 서로의 echo를 오독한다(TBD-HW-010).
      frontUltrasonicValid = measureUltrasonicMm(HC_TRIG_PIN, HC_ECHO_PIN, frontDistanceMm);
      g_frontUltrasonicFailStreak = frontUltrasonicValid ? 0 : (g_frontUltrasonicFailStreak + 1);
      rearUltrasonicValid = measureUltrasonicMm(HC_REAR_TRIG_PIN, HC_REAR_ECHO_PIN, rearDistanceMm);
      g_rearUltrasonicFailStreak = rearUltrasonicValid ? 0 : (g_rearUltrasonicFailStreak + 1);
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

    if (!ultrasonicUpdated && !dhtUpdated) {
      vTaskDelay(pdMS_TO_TICKS(ENV_TASK_INTERVAL_MS));
      continue;
    }

    sensorSharedStateUpdate([&](SensorSharedState& s) {
      if (ultrasonicUpdated) {
        if (frontUltrasonicValid) {
          s.frontMinDistanceMm = frontDistanceMm;
          s.validSensorMask |= PROXIMITY_FRONT_VALID_BIT;
          // 후방은 아직 protective_stop에 반영하지 않는다(위 PROXIMITY_STOP_DISTANCE_MM
          // 주석 참고, TBD-HW-011). 전방도 발동이 꺼져 있다(PROXIMITY_STOP_ENABLED,
          // S15P11A301-353) - 거리 측정·발행은 유지하고 정지 권한만 뺐다.
          s.protectiveStop = (PROXIMITY_STOP_ENABLED &&
                              frontDistanceMm <= PROXIMITY_STOP_DISTANCE_MM) ? 1 : 0;
        } else {
          s.validSensorMask &= ~PROXIMITY_FRONT_VALID_BIT;
        }

        if (rearUltrasonicValid) {
          s.rearMinDistanceMm = rearDistanceMm;
          s.validSensorMask |= PROXIMITY_REAR_VALID_BIT;
        } else {
          s.validSensorMask &= ~PROXIMITY_REAR_VALID_BIT;
        }

        const bool proximityFault =
            g_frontUltrasonicFailStreak >= PROXIMITY_FAULT_STREAK_THRESHOLD ||
            g_rearUltrasonicFailStreak >= PROXIMITY_FAULT_STREAK_THRESHOLD;
        if (proximityFault) {
          s.faultFlags |= FAULT_PROXIMITY_SENSOR_FAULT;
        } else if (frontUltrasonicValid || rearUltrasonicValid) {
          s.faultFlags &= ~FAULT_PROXIMITY_SENSOR_FAULT;
        }
        s.lastProximityUpdateMs = now;
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
        s.lastEnvironmentUpdateMs = now;
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
    });

    vTaskDelay(pdMS_TO_TICKS(ENV_TASK_INTERVAL_MS));
  }
}

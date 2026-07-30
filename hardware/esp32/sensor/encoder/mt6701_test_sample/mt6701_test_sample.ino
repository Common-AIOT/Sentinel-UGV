#include <Wire.h>

/*
 * ============================================================
 * 사용자 설정 영역
 * 아래 값들은 실제 하드웨어에 맞게 수동으로 수정하세요.
 * ============================================================
 */

// ESP32와 MT6701의 I2C 연결 핀
constexpr int I2C_SDA_PIN = 21;  // 필요 시 SDA GPIO 번호 수정
constexpr int I2C_SCL_PIN = 22;  // 필요 시 SCL GPIO 번호 수정

/*
 * 감속비 설정
 *
 * 출력축을 1바퀴 돌렸을 때 MT6701이 82바퀴로 측정됐다면
 * 우선 82.0으로 설정합니다.
 *
 * 출력축에 자석이 직접 설치된 경우에는 1.0으로 설정하세요.
 */
constexpr double GEAR_RATIO = 82.0;  // ★ 사용자가 보정할 핵심 값

/*
 * 출력축 각도 증가 방향 설정
 *
 * 원하는 방향으로 회전했을 때 값이 양수이면 1.0
 * 원하는 방향으로 회전했을 때 값이 음수이면 -1.0
 */
constexpr double DIRECTION_SIGN = 1.0;  // 필요 시 -1.0으로 수정

// 시리얼 모니터 속도
constexpr unsigned long SERIAL_BAUD_RATE = 115200;

// I2C 통신 속도
constexpr uint32_t I2C_CLOCK_SPEED = 400000;

// 시리얼 출력 간격
constexpr unsigned long PRINT_INTERVAL_MS = 50;

// 센서 읽기 간격
constexpr unsigned long SENSOR_SAMPLE_INTERVAL_MS = 2;

/*
 * ============================================================
 * 사용자 설정 영역 끝
 * 아래 부분은 일반적으로 수정하지 않아도 됩니다.
 * ============================================================
 */

constexpr uint8_t MT6701_ADDR_DEFAULT = 0x06;
constexpr uint8_t MT6701_ADDR_ALT     = 0x46;

constexpr uint8_t REG_ANGLE_HIGH = 0x03;
constexpr uint8_t REG_ANGLE_LOW  = 0x04;

uint8_t mt6701Address = 0;

float previousAbsoluteAngle = 0.0f;
bool previousAngleValid = false;

// MT6701이 장착된 센서축의 누적 회전각
double sensorAccumulatedAngle = 0.0;

unsigned long lastPrintTime = 0;
unsigned long lastSampleTime = 0;
unsigned long lastErrorTime = 0;

/*
 * 지정한 I2C 주소에 장치가 있는지 확인
 */
bool pingDevice(uint8_t address)
{
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

/*
 * MT6701 주소 자동 확인
 */
uint8_t findMT6701()
{
  if (pingDevice(MT6701_ADDR_DEFAULT)) {
    return MT6701_ADDR_DEFAULT;
  }

  if (pingDevice(MT6701_ADDR_ALT)) {
    return MT6701_ADDR_ALT;
  }

  return 0;
}

/*
 * MT6701 레지스터 1바이트 읽기
 */
bool readRegister(uint8_t reg, uint8_t &value)
{
  Wire.beginTransmission(mt6701Address);
  Wire.write(reg);

  // STOP을 발생시키지 않고 repeated START 사용
  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  if (Wire.requestFrom(mt6701Address, (uint8_t)1, true) != 1) {
    return false;
  }

  value = Wire.read();
  return true;
}

/*
 * MT6701의 14비트 절대각 읽기
 *
 * rawAngle:
 *   0~16383
 *
 * angleDegrees:
 *   0~360도
 */
bool readMT6701Angle(uint16_t &rawAngle, float &angleDegrees)
{
  uint8_t highByte;
  uint8_t lowByte;

  // 데이터시트에서 지정한 순서대로 읽기
  if (!readRegister(REG_ANGLE_HIGH, highByte)) {
    return false;
  }

  if (!readRegister(REG_ANGLE_LOW, lowByte)) {
    return false;
  }

  rawAngle =
      (static_cast<uint16_t>(highByte) << 6) |
      (lowByte >> 2);

  angleDegrees = rawAngle * (360.0f / 16384.0f);

  return true;
}

/*
 * 0도와 360도 경계를 고려한 각도 차이 계산
 *
 * 예시:
 *   이전 각도 359도
 *   현재 각도   1도
 *   실제 이동  +2도
 */
float calculateAngleDifference(float currentAngle, float previousAngle)
{
  float difference = currentAngle - previousAngle;

  if (difference > 180.0f) {
    difference -= 360.0f;
  } else if (difference < -180.0f) {
    difference += 360.0f;
  }

  return difference;
}

/*
 * 누적 각도 초기화
 *
 * 시리얼 모니터에서 숫자 1을 입력하면 실행됩니다.
 */
void resetAccumulatedAngle()
{
  sensorAccumulatedAngle = 0.0;

  // 초기화 시점의 현재 위치를 새로운 기준으로 사용
  previousAngleValid = false;

  Serial.println();
  Serial.println("========================================");
  Serial.println("출력축 이동 각도를 0도로 초기화했습니다.");
  Serial.println("========================================");
}

/*
 * 시리얼 모니터 명령 처리
 */
void handleSerialCommand()
{
  while (Serial.available() > 0) {
    char command = Serial.read();

    if (command == '1') {
      resetAccumulatedAngle();
    }
  }
}

void setup()
{
  Serial.begin(SERIAL_BAUD_RATE);
  delay(500);

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(I2C_CLOCK_SPEED);

  mt6701Address = findMT6701();

  Serial.println();
  Serial.println("MT6701 누적 각도 테스트 시작");

  if (mt6701Address == 0) {
    Serial.println("MT6701을 찾지 못했습니다.");
    Serial.println("SDA, SCL, VDD, GND 및 I2C 모드를 확인하세요.");
    return;
  }

  Serial.printf(
      "MT6701 발견: I2C 주소 0x%02X\n",
      mt6701Address
  );

  Serial.printf(
      "현재 감속비 설정: %.4f : 1\n",
      GEAR_RATIO
  );

  Serial.println("시리얼 모니터에 숫자 1을 입력하면 초기화됩니다.");
  Serial.println();
}

void loop()
{
  handleSerialCommand();

  if (mt6701Address == 0) {
    delay(1000);
    return;
  }

  unsigned long currentTime = millis();

  if (currentTime - lastSampleTime < SENSOR_SAMPLE_INTERVAL_MS) {
    return;
  }

  lastSampleTime = currentTime;

  uint16_t rawAngle = 0;
  float currentAbsoluteAngle = 0.0f;

  if (!readMT6701Angle(rawAngle, currentAbsoluteAngle)) {
    if (currentTime - lastErrorTime >= 500) {
      lastErrorTime = currentTime;
      Serial.println("MT6701 읽기 실패");
    }

    return;
  }

  if (!previousAngleValid) {
    // 최초 측정값 또는 초기화 직후 측정값을 기준점으로 저장
    previousAbsoluteAngle = currentAbsoluteAngle;
    previousAngleValid = true;
  } else {
    float angleDifference = calculateAngleDifference(
        currentAbsoluteAngle,
        previousAbsoluteAngle
    );

    // 센서축 회전각 누적
    sensorAccumulatedAngle += angleDifference;

    previousAbsoluteAngle = currentAbsoluteAngle;
  }

  if (currentTime - lastPrintTime >= PRINT_INTERVAL_MS) {
    lastPrintTime = currentTime;

    /*
     * 센서축 누적 회전수
     */
    double sensorRevolutions =
        sensorAccumulatedAngle / 360.0;

    /*
     * 출력축 누적각 계산
     *
     * 센서축 누적각 ÷ 감속비
     */
    double outputAccumulatedAngle =
        (sensorAccumulatedAngle / GEAR_RATIO) *
        DIRECTION_SIGN;

    /*
     * 출력축 누적 회전수
     */
    double outputRevolutions =
        outputAccumulatedAngle / 360.0;

    Serial.printf(
        "현재 절대각: %7.2f° | "
        "센서축: %8.3f회 | "
        "출력축: %9.2f° | "
        "출력축 회전수: %7.3f회\n",
        currentAbsoluteAngle,
        sensorRevolutions,
        outputAccumulatedAngle,
        outputRevolutions
    );
  }
}
#include <Wire.h>

/*
 * ============================================================
 * 사용자 설정 영역
 * ============================================================
 */

// 두 센서가 공유하는 I2C 버스
constexpr int DRIVE_I2C_SDA_PIN = 21;
constexpr int DRIVE_I2C_SCL_PIN = 22;

// 주소로 왼쪽과 오른쪽 구분
constexpr uint8_t LEFT_ENCODER_ADDRESS  = 0x06;
constexpr uint8_t RIGHT_ENCODER_ADDRESS = 0x46;

/*
 * ★ 양쪽 바퀴의 감속비를 개별적으로 수정하세요.
 *
 * 바퀴를 정확히 1회전시켰을 때 표시되는
 * 센서축 회전수의 절댓값을 입력합니다.
 */
constexpr double LEFT_GEAR_RATIO  = 82;
constexpr double RIGHT_GEAR_RATIO = 82;

/*
 * ★ 방향 보정
 *
 * 차량이 전진하는 방향으로 돌렸을 때:
 *   양수로 나오면  1.0
 *   음수로 나오면 -1.0
 *
 * 좌우 모터가 대칭으로 설치된 경우
 * 한쪽은 -1.0이 필요할 수 있습니다.
 */
constexpr double LEFT_DIRECTION_SIGN  = 1.0;
constexpr double RIGHT_DIRECTION_SIGN = 1.0;

constexpr uint32_t I2C_CLOCK_SPEED = 400000;
constexpr unsigned long SERIAL_BAUD_RATE = 115200;
constexpr unsigned long SAMPLE_INTERVAL_MS = 2;
constexpr unsigned long PRINT_INTERVAL_MS = 500;

/*
 * ============================================================
 * 사용자 설정 영역 끝
 * ============================================================
 */

constexpr uint8_t REG_ANGLE_HIGH = 0x03;
constexpr uint8_t REG_ANGLE_LOW  = 0x04;

struct EncoderState
{
  const char *name;
  uint8_t address;

  uint16_t rawAngle;
  float absoluteAngle;
  float previousAbsoluteAngle;

  double sensorAccumulatedAngle;

  bool previousAngleValid;
  bool online;

  EncoderState(const char *encoderName, uint8_t i2cAddress)
      : name(encoderName),
        address(i2cAddress),
        rawAngle(0),
        absoluteAngle(0.0f),
        previousAbsoluteAngle(0.0f),
        sensorAccumulatedAngle(0.0),
        previousAngleValid(false),
        online(false)
  {
  }
};

EncoderState leftEncoder(
    "LEFT",
    LEFT_ENCODER_ADDRESS
);

EncoderState rightEncoder(
    "RIGHT",
    RIGHT_ENCODER_ADDRESS
);

unsigned long lastSampleTime = 0;
unsigned long lastPrintTime = 0;
unsigned long lastErrorTime = 0;

/*
 * I2C 주소에 장치가 있는지 확인
 */
bool pingDevice(uint8_t address)
{
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

/*
 * MT6701 레지스터 1바이트 읽기
 */
bool readRegister(
    uint8_t deviceAddress,
    uint8_t registerAddress,
    uint8_t &value)
{
  Wire.beginTransmission(deviceAddress);
  Wire.write(registerAddress);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  if (Wire.requestFrom(
          deviceAddress,
          (uint8_t)1,
          true) != 1) {
    return false;
  }

  value = Wire.read();
  return true;
}

/*
 * MT6701 14비트 절대각 읽기
 */
bool readMT6701Angle(
    uint8_t address,
    uint16_t &rawAngle,
    float &angleDegrees)
{
  uint8_t highByte;
  uint8_t lowByte;

  // 데이터시트 지정 순서: 0x03을 먼저 읽음
  if (!readRegister(
          address,
          REG_ANGLE_HIGH,
          highByte)) {
    return false;
  }

  if (!readRegister(
          address,
          REG_ANGLE_LOW,
          lowByte)) {
    return false;
  }

  rawAngle =
      (static_cast<uint16_t>(highByte) << 6) |
      (lowByte >> 2);

  angleDegrees = rawAngle * (360.0f / 16384.0f);

  return true;
}

/*
 * 0도와 360도 경계를 고려한 이동각 계산
 */
float calculateAngleDifference(
    float currentAngle,
    float previousAngle)
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
 * 센서 하나를 읽고 이동각 누적
 */
bool updateEncoder(EncoderState &encoder)
{
  uint16_t newRawAngle;
  float newAbsoluteAngle;

  if (!readMT6701Angle(
          encoder.address,
          newRawAngle,
          newAbsoluteAngle)) {
    return false;
  }

  encoder.rawAngle = newRawAngle;
  encoder.absoluteAngle = newAbsoluteAngle;

  if (!encoder.previousAngleValid) {
    encoder.previousAbsoluteAngle = newAbsoluteAngle;
    encoder.previousAngleValid = true;
    return true;
  }

  float angleDifference = calculateAngleDifference(
      newAbsoluteAngle,
      encoder.previousAbsoluteAngle
  );

  encoder.sensorAccumulatedAngle += angleDifference;
  encoder.previousAbsoluteAngle = newAbsoluteAngle;

  return true;
}

/*
 * 선택한 센서 초기화
 */
void resetEncoder(EncoderState &encoder)
{
  encoder.sensorAccumulatedAngle = 0.0;
  encoder.previousAngleValid = false;

  Serial.printf(
      "%s 바퀴 누적값을 초기화했습니다.\n",
      encoder.name
  );
}

/*
 * 시리얼 명령 처리
 */
void handleSerialCommand()
{
  while (Serial.available() > 0) {
    char command = Serial.read();

    switch (command) {
      case '1':
        resetEncoder(leftEncoder);
        break;

      case '2':
        resetEncoder(rightEncoder);
        break;

      case '0':
        resetEncoder(leftEncoder);
        resetEncoder(rightEncoder);
        Serial.println("양쪽 바퀴를 모두 초기화했습니다.");
        break;

      default:
        // 줄바꿈 문자 등은 무시
        break;
    }
  }
}

/*
 * 센서 결과 출력
 */
void printEncoderResult(
    const EncoderState &encoder,
    double gearRatio,
    double directionSign)
{
  if (!encoder.online) {
    Serial.printf(
        "%s [0x%02X]: 연결 안 됨\n",
        encoder.name,
        encoder.address
    );

    return;
  }

  double sensorRevolutions =
      encoder.sensorAccumulatedAngle / 360.0;

  double wheelAccumulatedAngle =
      (encoder.sensorAccumulatedAngle / gearRatio) *
      directionSign;

  double wheelRevolutions =
      wheelAccumulatedAngle / 360.0;

  Serial.printf(
      "%s [0x%02X] | "
      "현재: %7.2f deg | "
      "센서축: %9.3f rev | "
      "바퀴각: %10.2f deg | "
      "바퀴회전: %8.3f rev\n",
      encoder.name,
      encoder.address,
      encoder.absoluteAngle,
      sensorRevolutions,
      wheelAccumulatedAngle,
      wheelRevolutions
  );
}

void setup()
{
  Serial.begin(SERIAL_BAUD_RATE);
  delay(500);

  Wire.begin(
      DRIVE_I2C_SDA_PIN,
      DRIVE_I2C_SCL_PIN
  );

  Wire.setClock(I2C_CLOCK_SPEED);

  leftEncoder.online =
      pingDevice(LEFT_ENCODER_ADDRESS);

  rightEncoder.online =
      pingDevice(RIGHT_ENCODER_ADDRESS);

  Serial.println();
  Serial.println("좌우 주행 바퀴 MT6701 테스트");
  Serial.println("========================================");

  Serial.printf(
      "LEFT  [0x%02X]: %s\n",
      leftEncoder.address,
      leftEncoder.online ? "연결됨" : "연결 안 됨"
  );

  Serial.printf(
      "RIGHT [0x%02X]: %s\n",
      rightEncoder.address,
      rightEncoder.online ? "연결됨" : "연결 안 됨"
  );

  Serial.println("----------------------------------------");

  Serial.printf(
      "LEFT 감속비 : %.4f\n",
      LEFT_GEAR_RATIO
  );

  Serial.printf(
      "RIGHT 감속비: %.4f\n",
      RIGHT_GEAR_RATIO
  );

  Serial.println("----------------------------------------");
  Serial.println("1: 왼쪽 초기화");
  Serial.println("2: 오른쪽 초기화");
  Serial.println("0: 양쪽 모두 초기화");
  Serial.println("========================================");
}

void loop()
{
  handleSerialCommand();

  unsigned long currentTime = millis();

  if (currentTime - lastSampleTime >= SAMPLE_INTERVAL_MS) {
    lastSampleTime = currentTime;

    bool leftReadOK = true;
    bool rightReadOK = true;

    // 같은 버스에서 주소별로 순차 읽기
    if (leftEncoder.online) {
      leftReadOK = updateEncoder(leftEncoder);
    }

    if (rightEncoder.online) {
      rightReadOK = updateEncoder(rightEncoder);
    }

    if ((!leftReadOK || !rightReadOK) &&
        currentTime - lastErrorTime >= 500) {
      lastErrorTime = currentTime;

      if (!leftReadOK) {
        Serial.println("LEFT I2C 읽기 실패");
      }

      if (!rightReadOK) {
        Serial.println("RIGHT I2C 읽기 실패");
      }
    }
  }

  if (currentTime - lastPrintTime >= PRINT_INTERVAL_MS) {
    lastPrintTime = currentTime;

    printEncoderResult(
        leftEncoder,
        LEFT_GEAR_RATIO,
        LEFT_DIRECTION_SIGN
    );

    printEncoderResult(
        rightEncoder,
        RIGHT_GEAR_RATIO,
        RIGHT_DIRECTION_SIGN
    );

    Serial.println();
  }
}
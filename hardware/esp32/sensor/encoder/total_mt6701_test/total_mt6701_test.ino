#include <Arduino.h>
#include <Wire.h>

/*
 * ============================================================
 * 사용자가 수정할 설정
 * ============================================================
 */

// ------------------------------------------------------------
// I2C 버스 0: 주행 모터 엔코더 두 개
// ------------------------------------------------------------
constexpr int DRIVE_I2C_SDA_PIN = 21;
constexpr int DRIVE_I2C_SCL_PIN = 22;

constexpr uint8_t LEFT_ENCODER_ADDRESS  = 0x06;
constexpr uint8_t RIGHT_ENCODER_ADDRESS = 0x46;

// ------------------------------------------------------------
// I2C 버스 1: 추가 엔코더 한 개
// ------------------------------------------------------------
constexpr int STEERING_I2C_SDA_PIN = 32;
constexpr int STEERING_I2C_SCL_PIN = 33;

// 다른 I2C 버스이므로 0x06을 다시 사용해도 됩니다.
constexpr uint8_t STEERING_ENCODER_ADDRESS = 0x06;

// ------------------------------------------------------------
// [수동 수정] 엔코더별 감속비
// ------------------------------------------------------------
constexpr double LEFT_GEAR_RATIO     = 82.0;
constexpr double RIGHT_GEAR_RATIO    = 82.0;

// 추가 센서가 출력축에 직접 달려 있다면 1.0
// 모터축에 달려 있고 감속기가 있다면 실제 감속비로 수정
constexpr double STEERING_GEAR_RATIO = 82.0;

// ------------------------------------------------------------
// [수동 수정] 엔코더별 회전 방향
//
// 정상 방향이면 1.0
// 방향을 반대로 표시하려면 -1.0
// ------------------------------------------------------------
constexpr double LEFT_DIRECTION_SIGN     = 1.0;
constexpr double RIGHT_DIRECTION_SIGN    = 1.0;
constexpr double STEERING_DIRECTION_SIGN = 1.0;

// ------------------------------------------------------------
// 통신 및 출력 설정
// ------------------------------------------------------------

// 배선이 짧고 안정적이면 400000 사용 가능
// 통신이 불안정하면 100000으로 변경
constexpr uint32_t I2C_CLOCK_SPEED = 400000;

constexpr unsigned long SERIAL_BAUD_RATE = 115200;

// 센서 측정 주기: 2ms
constexpr unsigned long SAMPLE_INTERVAL_MS = 2;

// 시리얼 출력 주기: 500ms = 0.5초
constexpr unsigned long PRINT_INTERVAL_MS = 500;

// 연결이 끊긴 센서 재검색 주기
constexpr unsigned long RECONNECT_INTERVAL_MS = 1000;

/*
 * ============================================================
 * 설정 영역 끝
 * ============================================================
 */

// ESP32 I2C 하드웨어 컨트롤러 0
TwoWire &driveWire = Wire;

// ESP32 I2C 하드웨어 컨트롤러 1
TwoWire steeringWire(1);

constexpr uint8_t REG_ANGLE_HIGH = 0x03;
constexpr uint8_t REG_ANGLE_LOW  = 0x04;

struct EncoderState
{
    const char *name;
    TwoWire *bus;
    uint8_t address;

    uint16_t rawAngle;
    float absoluteAngle;
    float previousAbsoluteAngle;

    double sensorAccumulatedAngle;

    bool previousAngleValid;
    bool online;

    EncoderState(
        const char *encoderName,
        TwoWire &encoderBus,
        uint8_t i2cAddress
    )
        : name(encoderName),
          bus(&encoderBus),
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
    driveWire,
    LEFT_ENCODER_ADDRESS
);

EncoderState rightEncoder(
    "RIGHT",
    driveWire,
    RIGHT_ENCODER_ADDRESS
);

EncoderState steeringEncoder(
    "STEERING",
    steeringWire,
    STEERING_ENCODER_ADDRESS
);

unsigned long lastSampleTime = 0;
unsigned long lastPrintTime = 0;
unsigned long lastErrorTime = 0;
unsigned long lastReconnectTime = 0;

/*
 * 지정된 I2C 버스에서 주소가 응답하는지 확인
 */
bool pingDevice(
    TwoWire &bus,
    uint8_t address
)
{
    bus.beginTransmission(address);
    return bus.endTransmission() == 0;
}

/*
 * 특정 I2C 버스의 전체 주소 검색
 */
void scanI2CBus(
    TwoWire &bus,
    const char *busName
)
{
    int foundCount = 0;

    Serial.println();
    Serial.printf("[%s] address scan\n", busName);

    for (uint8_t address = 1; address < 127; address++) {
        bus.beginTransmission(address);
        uint8_t error = bus.endTransmission();

        if (error == 0) {
            Serial.printf(
                "  Found: 0x%02X\n",
                address
            );

            foundCount++;
        }
    }

    if (foundCount == 0) {
        Serial.println("  No I2C devices found");
    }
}

/*
 * MT6701 레지스터 한 바이트 읽기
 */
bool readRegister(
    TwoWire &bus,
    uint8_t deviceAddress,
    uint8_t registerAddress,
    uint8_t &value
)
{
    bus.beginTransmission(deviceAddress);
    bus.write(registerAddress);

    if (bus.endTransmission(false) != 0) {
        return false;
    }

    if (bus.requestFrom(
            deviceAddress,
            static_cast<uint8_t>(1),
            true
        ) != 1) {
        return false;
    }

    value = bus.read();
    return true;
}

/*
 * MT6701 14비트 절대각 읽기
 */
bool readMT6701Angle(
    TwoWire &bus,
    uint8_t address,
    uint16_t &rawAngle,
    float &angleDegrees
)
{
    uint8_t highByte = 0;
    uint8_t lowByte = 0;

    // MT6701은 반드시 0x03을 먼저 읽고
    // 그다음 0x04를 읽습니다.
    if (!readRegister(
            bus,
            address,
            REG_ANGLE_HIGH,
            highByte
        )) {
        return false;
    }

    if (!readRegister(
            bus,
            address,
            REG_ANGLE_LOW,
            lowByte
        )) {
        return false;
    }

    rawAngle =
        (static_cast<uint16_t>(highByte) << 6) |
        (lowByte >> 2);

    angleDegrees =
        rawAngle * (360.0f / 16384.0f);

    return true;
}

/*
 * 0도와 360도 경계를 고려한 각도 차이 계산
 */
float calculateAngleDifference(
    float currentAngle,
    float previousAngle
)
{
    float difference =
        currentAngle - previousAngle;

    if (difference > 180.0f) {
        difference -= 360.0f;
    }
    else if (difference < -180.0f) {
        difference += 360.0f;
    }

    return difference;
}

/*
 * 엔코더 값 읽기 및 누적 각도 계산
 */
bool updateEncoder(EncoderState &encoder)
{
    uint16_t newRawAngle = 0;
    float newAbsoluteAngle = 0.0f;

    if (!readMT6701Angle(
            *encoder.bus,
            encoder.address,
            newRawAngle,
            newAbsoluteAngle
        )) {
        return false;
    }

    encoder.rawAngle = newRawAngle;
    encoder.absoluteAngle = newAbsoluteAngle;

    if (!encoder.previousAngleValid) {
        encoder.previousAbsoluteAngle =
            newAbsoluteAngle;

        encoder.previousAngleValid = true;
        return true;
    }

    float angleDifference =
        calculateAngleDifference(
            newAbsoluteAngle,
            encoder.previousAbsoluteAngle
        );

    encoder.sensorAccumulatedAngle +=
        angleDifference;

    encoder.previousAbsoluteAngle =
        newAbsoluteAngle;

    return true;
}

/*
 * 엔코더 누적값 초기화
 */
void resetEncoder(EncoderState &encoder)
{
    encoder.sensorAccumulatedAngle = 0.0;
    encoder.previousAngleValid = false;

    Serial.printf(
        "%s accumulated angle reset\n",
        encoder.name
    );
}

/*
 * 연결이 끊긴 센서를 다시 검색
 */
void reconnectEncoder(EncoderState &encoder)
{
    if (encoder.online) {
        return;
    }

    if (pingDevice(
            *encoder.bus,
            encoder.address
        )) {
        encoder.online = true;
        encoder.previousAngleValid = false;

        Serial.printf(
            "%s [0x%02X] connected\n",
            encoder.name,
            encoder.address
        );
    }
}

/*
 * 시리얼 명령 처리
 *
 * 1: 왼쪽 초기화
 * 2: 오른쪽 초기화
 * 3: 추가/조향 초기화
 * 0: 전체 초기화
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

            case '3':
                resetEncoder(steeringEncoder);
                break;

            case '0':
                resetEncoder(leftEncoder);
                resetEncoder(rightEncoder);
                resetEncoder(steeringEncoder);

                Serial.println(
                    "All encoders reset"
                );
                break;

            default:
                // 줄바꿈 문자 등은 무시
                break;
        }
    }
}

/*
 * 엔코더 결과 출력
 */
void printEncoderResult(
    const EncoderState &encoder,
    double gearRatio,
    double directionSign
)
{
    if (!encoder.online) {
        Serial.printf(
            "%s [0x%02X]: OFFLINE\n",
            encoder.name,
            encoder.address
        );

        return;
    }

    double sensorRevolutions =
        encoder.sensorAccumulatedAngle / 360.0;

    double outputAccumulatedAngle =
        (encoder.sensorAccumulatedAngle / gearRatio) *
        directionSign;

    double outputRevolutions =
        outputAccumulatedAngle / 360.0;

    Serial.printf(
        "%s [0x%02X] | "
        "ABS: %7.2f deg | "
        "SENSOR: %9.3f rev | "
        "OUTPUT: %10.2f deg | "
        "OUT REV: %8.3f\n",
        encoder.name,
        encoder.address,
        encoder.absoluteAngle,
        sensorRevolutions,
        outputAccumulatedAngle,
        outputRevolutions
    );
}

void setup()
{
    Serial.begin(SERIAL_BAUD_RATE);
    delay(500);

    /*
     * I2C 버스 0 시작
     * GPIO21 SDA / GPIO22 SCL
     */
    driveWire.begin(
        DRIVE_I2C_SDA_PIN,
        DRIVE_I2C_SCL_PIN
    );

    driveWire.setClock(I2C_CLOCK_SPEED);
    driveWire.setTimeOut(100);

    /*
     * I2C 버스 1 시작
     * GPIO32 SDA / GPIO33 SCL
     */
    steeringWire.begin(
        STEERING_I2C_SDA_PIN,
        STEERING_I2C_SCL_PIN
    );

    steeringWire.setClock(I2C_CLOCK_SPEED);
    steeringWire.setTimeOut(100);

    delay(100);

    // 시작 시 각 버스 주소 검색
    scanI2CBus(
        driveWire,
        "DRIVE BUS 0, GPIO21/22"
    );

    scanI2CBus(
        steeringWire,
        "STEERING BUS 1, GPIO32/33"
    );

    // 센서 연결 상태 확인
    leftEncoder.online =
        pingDevice(
            driveWire,
            LEFT_ENCODER_ADDRESS
        );

    rightEncoder.online =
        pingDevice(
            driveWire,
            RIGHT_ENCODER_ADDRESS
        );

    steeringEncoder.online =
        pingDevice(
            steeringWire,
            STEERING_ENCODER_ADDRESS
        );

    Serial.println();
    Serial.println("========================================");
    Serial.println("MT6701 dual-I2C-bus test");
    Serial.println("========================================");

    Serial.printf(
        "LEFT     bus0 [0x%02X]: %s\n",
        leftEncoder.address,
        leftEncoder.online ? "ONLINE" : "OFFLINE"
    );

    Serial.printf(
        "RIGHT    bus0 [0x%02X]: %s\n",
        rightEncoder.address,
        rightEncoder.online ? "ONLINE" : "OFFLINE"
    );

    Serial.printf(
        "STEERING bus1 [0x%02X]: %s\n",
        steeringEncoder.address,
        steeringEncoder.online ? "ONLINE" : "OFFLINE"
    );

    Serial.println("----------------------------------------");
    Serial.println("1: reset LEFT");
    Serial.println("2: reset RIGHT");
    Serial.println("3: reset STEERING");
    Serial.println("0: reset ALL");
    Serial.println("========================================");
}

void loop()
{
    handleSerialCommand();

    unsigned long currentTime = millis();

    /*
     * 센서 주기적 읽기
     */
    if (currentTime - lastSampleTime >=
        SAMPLE_INTERVAL_MS) {

        lastSampleTime = currentTime;

        bool leftReadOK = true;
        bool rightReadOK = true;
        bool steeringReadOK = true;

        if (leftEncoder.online) {
            leftReadOK =
                updateEncoder(leftEncoder);

            if (!leftReadOK) {
                leftEncoder.online = false;
            }
        }

        if (rightEncoder.online) {
            rightReadOK =
                updateEncoder(rightEncoder);

            if (!rightReadOK) {
                rightEncoder.online = false;
            }
        }

        if (steeringEncoder.online) {
            steeringReadOK =
                updateEncoder(steeringEncoder);

            if (!steeringReadOK) {
                steeringEncoder.online = false;
            }
        }

        if ((!leftReadOK ||
             !rightReadOK ||
             !steeringReadOK) &&
            currentTime - lastErrorTime >= 500) {

            lastErrorTime = currentTime;

            if (!leftReadOK) {
                Serial.println(
                    "LEFT I2C read failed"
                );
            }

            if (!rightReadOK) {
                Serial.println(
                    "RIGHT I2C read failed"
                );
            }

            if (!steeringReadOK) {
                Serial.println(
                    "STEERING I2C read failed"
                );
            }
        }
    }

    /*
     * 연결되지 않은 센서 재검색
     */
    if (currentTime - lastReconnectTime >=
        RECONNECT_INTERVAL_MS) {

        lastReconnectTime = currentTime;

        reconnectEncoder(leftEncoder);
        reconnectEncoder(rightEncoder);
        reconnectEncoder(steeringEncoder);
    }

    /*
     * 시리얼 모니터 결과 출력
     */
    if (currentTime - lastPrintTime >=
        PRINT_INTERVAL_MS) {

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

        printEncoderResult(
            steeringEncoder,
            STEERING_GEAR_RATIO,
            STEERING_DIRECTION_SIGN
        );

        Serial.println();
    }
}
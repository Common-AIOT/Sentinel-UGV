#include <Arduino.h>
#include <Wire.h>

/*
 * ESP32 integrated sensor test
 *
 * Serial monitor commands:
 *   1       : MT6701 monitor mode
 *   2       : HC-SR04 + DHT11 monitor mode
 *   L / R   : reset left / right MT6701 accumulated rotation
 *   S or 3  : reset steering MT6701 accumulated rotation
 *   A or 0  : reset all MT6701 accumulated rotations
 *   M / H / ?: print the command menu
 *
 * Use 115200 baud and any line-ending setting.
 */

// ============================================================
// Pin and device settings
// ============================================================

// I2C bus 0: left and right drive encoders
constexpr int DRIVE_I2C_SDA_PIN = 21;
constexpr int DRIVE_I2C_SCL_PIN = 22;

constexpr uint8_t LEFT_ENCODER_ADDRESS = 0x06;
constexpr uint8_t RIGHT_ENCODER_ADDRESS = 0x46;

// I2C bus 1: steering encoder
constexpr int STEERING_I2C_SDA_PIN = 32;
constexpr int STEERING_I2C_SCL_PIN = 33;
constexpr uint8_t STEERING_ENCODER_ADDRESS = 0x06;

// HC-SR04 and DHT11
constexpr uint8_t HC_TRIG_PIN = 5;
constexpr uint8_t HC_ECHO_PIN = 36;
constexpr uint8_t DHT_DATA_PIN = 4;

// Encoder reduction ratios
constexpr double LEFT_GEAR_RATIO = 82.0;
constexpr double RIGHT_GEAR_RATIO = 82.0;
constexpr double STEERING_GEAR_RATIO = 82.0;

// Set a sign to -1.0 when its displayed rotation must be reversed.
constexpr double LEFT_DIRECTION_SIGN = 1.0;
constexpr double RIGHT_DIRECTION_SIGN = 1.0;
constexpr double STEERING_DIRECTION_SIGN = 1.0;

// ============================================================
// Timing and communication settings
// ============================================================

constexpr uint32_t I2C_CLOCK_SPEED = 400000;
constexpr uint32_t SERIAL_BAUD_RATE = 115200;

constexpr uint32_t ENCODER_SAMPLE_INTERVAL_MS = 2;
constexpr uint32_t MT_PRINT_INTERVAL_MS = 500;
constexpr uint32_t RECONNECT_INTERVAL_MS = 1000;
constexpr uint32_t ERROR_PRINT_INTERVAL_MS = 500;

constexpr uint32_t ULTRASONIC_INTERVAL_MS = 250;
constexpr uint32_t DHT_INTERVAL_MS = 2000;

// HC-SR04 echo timeout: approximately a 5.1 m round trip.
constexpr uint32_t ECHO_TIMEOUT_US = 30000;
constexpr float SPEED_OF_SOUND_CM_PER_US = 0.0343f;
constexpr float MIN_VALID_DISTANCE_CM = 2.0f;
constexpr float MAX_VALID_DISTANCE_CM = 400.0f;

// ============================================================
// MT6701 state and functions
// ============================================================

TwoWire &driveWire = Wire;
TwoWire steeringWire(1);

constexpr uint8_t REG_ANGLE_HIGH = 0x03;
constexpr uint8_t REG_ANGLE_LOW = 0x04;

struct EncoderState {
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
      uint8_t i2cAddress)
      : name(encoderName),
        bus(&encoderBus),
        address(i2cAddress),
        rawAngle(0),
        absoluteAngle(0.0f),
        previousAbsoluteAngle(0.0f),
        sensorAccumulatedAngle(0.0),
        previousAngleValid(false),
        online(false) {
  }
};

EncoderState leftEncoder(
    "LEFT",
    driveWire,
    LEFT_ENCODER_ADDRESS);

EncoderState rightEncoder(
    "RIGHT",
    driveWire,
    RIGHT_ENCODER_ADDRESS);

EncoderState steeringEncoder(
    "STEERING",
    steeringWire,
    STEERING_ENCODER_ADDRESS);

bool pingDevice(TwoWire &bus, uint8_t address) {
  bus.beginTransmission(address);
  return bus.endTransmission() == 0;
}

void scanI2CBus(TwoWire &bus, const char *busName) {
  uint8_t foundCount = 0;

  Serial.println();
  Serial.printf("[%s] address scan\n", busName);

  for (uint8_t address = 1; address < 127; ++address) {
    bus.beginTransmission(address);
    const uint8_t error = bus.endTransmission();

    if (error == 0) {
      Serial.printf("  Found: 0x%02X\n", address);
      ++foundCount;
    }
  }

  if (foundCount == 0) {
    Serial.println("  No I2C devices found");
  }
}

bool readRegister(
    TwoWire &bus,
    uint8_t deviceAddress,
    uint8_t registerAddress,
    uint8_t &value) {
  bus.beginTransmission(deviceAddress);
  bus.write(registerAddress);

  if (bus.endTransmission(false) != 0) {
    return false;
  }

  if (bus.requestFrom(
          deviceAddress,
          static_cast<uint8_t>(1),
          true) != 1) {
    return false;
  }

  value = bus.read();
  return true;
}

bool readMT6701Angle(
    TwoWire &bus,
    uint8_t address,
    uint16_t &rawAngle,
    float &angleDegrees) {
  uint8_t highByte = 0;
  uint8_t lowByte = 0;

  if (!readRegister(
          bus,
          address,
          REG_ANGLE_HIGH,
          highByte)) {
    return false;
  }

  if (!readRegister(
          bus,
          address,
          REG_ANGLE_LOW,
          lowByte)) {
    return false;
  }

  rawAngle =
      (static_cast<uint16_t>(highByte) << 6) |
      (lowByte >> 2);

  angleDegrees =
      rawAngle * (360.0f / 16384.0f);

  return true;
}

float calculateAngleDifference(
    float currentAngle,
    float previousAngle) {
  float difference = currentAngle - previousAngle;

  if (difference > 180.0f) {
    difference -= 360.0f;
  } else if (difference < -180.0f) {
    difference += 360.0f;
  }

  return difference;
}

bool updateEncoder(EncoderState &encoder) {
  uint16_t newRawAngle = 0;
  float newAbsoluteAngle = 0.0f;

  if (!readMT6701Angle(
          *encoder.bus,
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

  const float angleDifference =
      calculateAngleDifference(
          newAbsoluteAngle,
          encoder.previousAbsoluteAngle);

  encoder.sensorAccumulatedAngle += angleDifference;
  encoder.previousAbsoluteAngle = newAbsoluteAngle;

  return true;
}

void resetEncoder(EncoderState &encoder) {
  encoder.sensorAccumulatedAngle = 0.0;
  encoder.previousAngleValid = false;

  Serial.printf(
      "[RESET] %s accumulated rotation reset\n",
      encoder.name);
}

void resetAllEncoders() {
  resetEncoder(leftEncoder);
  resetEncoder(rightEncoder);
  resetEncoder(steeringEncoder);
  Serial.println("[RESET] All encoders reset");
}

void reconnectEncoder(EncoderState &encoder) {
  if (encoder.online) {
    return;
  }

  if (pingDevice(*encoder.bus, encoder.address)) {
    encoder.online = true;
    encoder.previousAngleValid = false;

    Serial.printf(
        "[MT] %s [0x%02X] connected\n",
        encoder.name,
        encoder.address);
  }
}

void printEncoderResult(
    const EncoderState &encoder,
    double gearRatio,
    double directionSign) {
  if (!encoder.online) {
    Serial.printf(
        "[MT] %s [0x%02X] | OFFLINE\n",
        encoder.name,
        encoder.address);
    return;
  }

  const double sensorRevolutions =
      encoder.sensorAccumulatedAngle / 360.0;

  const double outputAccumulatedAngle =
      (encoder.sensorAccumulatedAngle / gearRatio) *
      directionSign;

  const double outputRevolutions =
      outputAccumulatedAngle / 360.0;

  Serial.printf(
      "[MT] %s [0x%02X] | "
      "ABS=%.2f deg | "
      "SENSOR_REV=%.3f | "
      "OUTPUT_DEG=%.2f | "
      "OUTPUT_REV=%.3f\n",
      encoder.name,
      encoder.address,
      encoder.absoluteAngle,
      sensorRevolutions,
      outputAccumulatedAngle,
      outputRevolutions);
}

// ============================================================
// HC-SR04 and DHT11 functions
// ============================================================

uint32_t ultrasonicMeasurementCount = 0;
uint32_t ultrasonicTimeoutCount = 0;
uint32_t dhtMeasurementCount = 0;
uint32_t dhtErrorCount = 0;

struct Dht11Reading {
  float temperatureC;
  float humidity;
};

bool readDht11(Dht11Reading &reading);

uint32_t readEchoTimeUs() {
  digitalWrite(HC_TRIG_PIN, LOW);
  delayMicroseconds(3);

  digitalWrite(HC_TRIG_PIN, HIGH);
  delayMicroseconds(12);
  digitalWrite(HC_TRIG_PIN, LOW);

  return pulseIn(HC_ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
}

void measureUltrasonic() {
  ++ultrasonicMeasurementCount;

  const uint32_t echoTimeUs = readEchoTimeUs();

  if (echoTimeUs == 0) {
    ++ultrasonicTimeoutCount;
    Serial.printf(
        "[US] SAMPLE=%lu | STATUS=TIMEOUT | TIMEOUTS=%lu\n",
        static_cast<unsigned long>(ultrasonicMeasurementCount),
        static_cast<unsigned long>(ultrasonicTimeoutCount));
    return;
  }

  const float distanceCm =
      echoTimeUs * SPEED_OF_SOUND_CM_PER_US * 0.5f;

  const char *status =
      (distanceCm >= MIN_VALID_DISTANCE_CM &&
       distanceCm <= MAX_VALID_DISTANCE_CM)
          ? "OK"
          : "OUT_OF_RANGE";

  Serial.printf(
      "[US] SAMPLE=%lu | ECHO_US=%lu | DISTANCE_CM=%.1f | STATUS=%s\n",
      static_cast<unsigned long>(ultrasonicMeasurementCount),
      static_cast<unsigned long>(echoTimeUs),
      distanceCm,
      status);
}

bool waitForDhtPinState(uint8_t state, uint32_t timeoutUs) {
  const uint32_t startUs = micros();

  while (digitalRead(DHT_DATA_PIN) != state) {
    if (micros() - startUs >= timeoutUs) {
      return false;
    }
  }

  return true;
}

bool readDht11(Dht11Reading &reading) {
  uint8_t data[5] = {0, 0, 0, 0, 0};

  // MCU start signal: hold DATA low for at least 18 ms.
  pinMode(DHT_DATA_PIN, OUTPUT);
  digitalWrite(DHT_DATA_PIN, LOW);
  delay(20);

  digitalWrite(DHT_DATA_PIN, HIGH);
  delayMicroseconds(30);
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  // Keep the DHT bit timing stable during the approximately 4 ms transfer.
  noInterrupts();

  bool success =
      waitForDhtPinState(LOW, 120) &&
      waitForDhtPinState(HIGH, 120) &&
      waitForDhtPinState(LOW, 120);

  for (uint8_t bitIndex = 0; bitIndex < 40 && success; ++bitIndex) {
    success = waitForDhtPinState(HIGH, 100);
    if (!success) {
      break;
    }

    const uint32_t highStartUs = micros();
    success = waitForDhtPinState(LOW, 120);
    if (!success) {
      break;
    }

    const uint32_t highTimeUs = micros() - highStartUs;
    data[bitIndex / 8] <<= 1;

    if (highTimeUs > 45) {
      data[bitIndex / 8] |= 1;
    }
  }

  interrupts();
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  if (!success) {
    return false;
  }

  const uint8_t expectedChecksum =
      static_cast<uint8_t>(
          data[0] + data[1] + data[2] + data[3]);

  if (data[4] != expectedChecksum) {
    return false;
  }

  reading.humidity = data[0] + data[1] * 0.1f;
  reading.temperatureC = data[2] + data[3] * 0.1f;
  return true;
}

void measureDht11() {
  ++dhtMeasurementCount;

  Dht11Reading reading = {};

  if (!readDht11(reading)) {
    ++dhtErrorCount;
    Serial.printf(
        "[DHT] SAMPLE=%lu | STATUS=READ_ERROR | ERRORS=%lu\n",
        static_cast<unsigned long>(dhtMeasurementCount),
        static_cast<unsigned long>(dhtErrorCount));
    return;
  }

  Serial.printf(
      "[DHT] SAMPLE=%lu | TEMPERATURE_C=%.1f | "
      "HUMIDITY_RH=%.1f | STATUS=OK\n",
      static_cast<unsigned long>(dhtMeasurementCount),
      reading.temperatureC,
      reading.humidity);
}

// ============================================================
// Mode, serial command, and scheduler functions
// ============================================================

enum class MonitorMode : uint8_t {
  MT6701 = 1,
  Environment = 2,
};

void setMonitorMode(MonitorMode newMode);

MonitorMode currentMode = MonitorMode::MT6701;

uint32_t lastEncoderSampleMs = 0;
uint32_t lastMtPrintMs = 0;
uint32_t lastErrorPrintMs = 0;
uint32_t lastReconnectMs = 0;
uint32_t lastUltrasonicMeasurementMs = 0;
uint32_t lastDhtMeasurementMs = 0;

void printMenu() {
  Serial.println();
  Serial.println("========== Integrated Sensor Menu ==========");
  Serial.println("1       : MT6701 monitor mode");
  Serial.println("2       : HC-SR04 + DHT11 monitor mode");
  Serial.println("L / R   : Reset LEFT / RIGHT rotation");
  Serial.println("S or 3  : Reset STEERING rotation");
  Serial.println("A or 0  : Reset ALL rotations");
  Serial.println("M / H / ?: Print this menu");
  Serial.println("============================================");
}

void setMonitorMode(MonitorMode newMode) {
  currentMode = newMode;
  const uint32_t nowMs = millis();

  Serial.println();

  if (currentMode == MonitorMode::MT6701) {
    lastMtPrintMs = nowMs - MT_PRINT_INTERVAL_MS;
    Serial.println("[MODE 1] MT6701 monitor");
  } else {
    lastUltrasonicMeasurementMs =
        nowMs - ULTRASONIC_INTERVAL_MS;

    // Do not force another DHT11 read when mode 2 is re-entered
    // before the sensor's minimum interval has elapsed.
    if (lastDhtMeasurementMs == 0) {
      lastDhtMeasurementMs = nowMs - DHT_INTERVAL_MS;
    }

    Serial.println("[MODE 2] HC-SR04 + DHT11 monitor");
  }
}

void handleSerialCommand() {
  while (Serial.available() > 0) {
    const char command =
        static_cast<char>(Serial.read());

    switch (command) {
      case '1':
        setMonitorMode(MonitorMode::MT6701);
        break;

      case '2':
        setMonitorMode(MonitorMode::Environment);
        break;

      case 'l':
      case 'L':
        resetEncoder(leftEncoder);
        break;

      case 'r':
      case 'R':
        resetEncoder(rightEncoder);
        break;

      case 's':
      case 'S':
      case '3':
        resetEncoder(steeringEncoder);
        break;

      case 'a':
      case 'A':
      case '0':
        resetAllEncoders();
        break;

      case 'm':
      case 'M':
      case 'h':
      case 'H':
      case '?':
        printMenu();
        break;

      // Ignore common serial-monitor line endings and whitespace.
      case '\r':
      case '\n':
      case ' ':
      case '\t':
        break;

      default:
        Serial.printf("[UNKNOWN] '%c'\n", command);
        printMenu();
        break;
    }
  }
}

void serviceEncoderSampling(uint32_t nowMs) {
  if (nowMs - lastEncoderSampleMs <
      ENCODER_SAMPLE_INTERVAL_MS) {
    return;
  }

  lastEncoderSampleMs = nowMs;

  bool leftReadOK = true;
  bool rightReadOK = true;
  bool steeringReadOK = true;

  if (leftEncoder.online) {
    leftReadOK = updateEncoder(leftEncoder);
    if (!leftReadOK) {
      leftEncoder.online = false;
    }
  }

  if (rightEncoder.online) {
    rightReadOK = updateEncoder(rightEncoder);
    if (!rightReadOK) {
      rightEncoder.online = false;
    }
  }

  if (steeringEncoder.online) {
    steeringReadOK = updateEncoder(steeringEncoder);
    if (!steeringReadOK) {
      steeringEncoder.online = false;
    }
  }

  if ((!leftReadOK ||
       !rightReadOK ||
       !steeringReadOK) &&
      nowMs - lastErrorPrintMs >=
          ERROR_PRINT_INTERVAL_MS) {
    lastErrorPrintMs = nowMs;

    if (!leftReadOK) {
      Serial.println("[MT] LEFT I2C read failed");
    }

    if (!rightReadOK) {
      Serial.println("[MT] RIGHT I2C read failed");
    }

    if (!steeringReadOK) {
      Serial.println("[MT] STEERING I2C read failed");
    }
  }
}

void serviceEncoderReconnect(uint32_t nowMs) {
  if (nowMs - lastReconnectMs < RECONNECT_INTERVAL_MS) {
    return;
  }

  lastReconnectMs = nowMs;
  reconnectEncoder(leftEncoder);
  reconnectEncoder(rightEncoder);
  reconnectEncoder(steeringEncoder);
}

void serviceMtMonitor(uint32_t nowMs) {
  if (nowMs - lastMtPrintMs < MT_PRINT_INTERVAL_MS) {
    return;
  }

  lastMtPrintMs = nowMs;

  printEncoderResult(
      leftEncoder,
      LEFT_GEAR_RATIO,
      LEFT_DIRECTION_SIGN);

  printEncoderResult(
      rightEncoder,
      RIGHT_GEAR_RATIO,
      RIGHT_DIRECTION_SIGN);

  printEncoderResult(
      steeringEncoder,
      STEERING_GEAR_RATIO,
      STEERING_DIRECTION_SIGN);

  Serial.println();
}

void serviceEnvironmentMonitor(uint32_t nowMs) {
  if (nowMs - lastUltrasonicMeasurementMs >=
      ULTRASONIC_INTERVAL_MS) {
    lastUltrasonicMeasurementMs = nowMs;
    measureUltrasonic();
  }

  if (nowMs - lastDhtMeasurementMs >= DHT_INTERVAL_MS) {
    lastDhtMeasurementMs = nowMs;
    measureDht11();
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);

  pinMode(HC_TRIG_PIN, OUTPUT);
  digitalWrite(HC_TRIG_PIN, LOW);
  pinMode(HC_ECHO_PIN, INPUT);
  pinMode(DHT_DATA_PIN, INPUT_PULLUP);

  driveWire.begin(
      DRIVE_I2C_SDA_PIN,
      DRIVE_I2C_SCL_PIN);
  driveWire.setClock(I2C_CLOCK_SPEED);
  driveWire.setTimeOut(100);

  steeringWire.begin(
      STEERING_I2C_SDA_PIN,
      STEERING_I2C_SCL_PIN);
  steeringWire.setClock(I2C_CLOCK_SPEED);
  steeringWire.setTimeOut(100);

  // Also gives the DHT11 enough time to stabilize after power-up.
  delay(1000);

  scanI2CBus(
      driveWire,
      "DRIVE BUS 0, GPIO21/22");

  scanI2CBus(
      steeringWire,
      "STEERING BUS 1, GPIO32/33");

  leftEncoder.online =
      pingDevice(
          driveWire,
          LEFT_ENCODER_ADDRESS);

  rightEncoder.online =
      pingDevice(
          driveWire,
          RIGHT_ENCODER_ADDRESS);

  steeringEncoder.online =
      pingDevice(
          steeringWire,
          STEERING_ENCODER_ADDRESS);

  Serial.println();
  Serial.println("============================================");
  Serial.println("ESP32 integrated sensor test ready");
  Serial.println("============================================");
  Serial.printf(
      "LEFT     bus0 [0x%02X]: %s\n",
      leftEncoder.address,
      leftEncoder.online ? "ONLINE" : "OFFLINE");
  Serial.printf(
      "RIGHT    bus0 [0x%02X]: %s\n",
      rightEncoder.address,
      rightEncoder.online ? "ONLINE" : "OFFLINE");
  Serial.printf(
      "STEERING bus1 [0x%02X]: %s\n",
      steeringEncoder.address,
      steeringEncoder.online ? "ONLINE" : "OFFLINE");
  Serial.printf(
      "HC-SR04  TRIG=GPIO%d | ECHO=GPIO%d\n",
      HC_TRIG_PIN,
      HC_ECHO_PIN);
  Serial.printf(
      "DHT11    DATA=GPIO%d\n",
      DHT_DATA_PIN);

  printMenu();
  setMonitorMode(MonitorMode::MT6701);
}

void loop() {
  handleSerialCommand();

  const uint32_t nowMs = millis();

  // Encoder sampling and reconnect checks remain active in both modes.
  serviceEncoderSampling(nowMs);
  serviceEncoderReconnect(nowMs);

  if (currentMode == MonitorMode::MT6701) {
    serviceMtMonitor(nowMs);
  } else {
    serviceEnvironmentMonitor(nowMs);
  }
}

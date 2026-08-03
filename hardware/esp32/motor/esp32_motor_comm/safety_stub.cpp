#include "safety_stub.h"

#include <Arduino.h>
#include <esp_arduino_version.h>

// 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
// 추가하지 말 것(README 참고).

namespace {

// tank_drive.ino(S15P11A301-195)에서 실측·검증한 배선과 동일하다. 조향 모터용
// BTS7960/RS380SP는 캐스터 휠로 대체되어 제거되었다.
constexpr uint8_t LEFT_RPWM_PIN = 25;
constexpr uint8_t LEFT_LPWM_PIN = 26;
constexpr uint8_t RIGHT_RPWM_PIN = 32;
constexpr uint8_t RIGHT_LPWM_PIN = 33;

// 두 주행 드라이버의 R_EN, L_EN을 함께 제어하는 공통 Enable.
constexpr uint8_t MOTOR_EN_PIN = 23;

// Arduino-ESP32 Core 2.x에서만 사용하는 LEDC 채널 번호.
constexpr uint8_t LEFT_RPWM_CHANNEL = 0;
constexpr uint8_t LEFT_LPWM_CHANNEL = 1;
constexpr uint8_t RIGHT_RPWM_CHANNEL = 2;
constexpr uint8_t RIGHT_LPWM_CHANNEL = 3;

constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr int16_t PWM_MAX = 255;
constexpr uint16_t DIRECTION_DEAD_TIME_MS = 50;

// docs/03-제어-캘리브레이션.md §35-4 실측 전 임시값: 이 mm/s에서 PWM=255(100%)로
// 포화되도록 선형 매핑한다. 폐루프 속도 보정은 Jetson이 센서 ESP32 엔코더
// 피드백으로 target_drive_*_mmps 자체를 갱신하는 방식으로 수행하므로(§34-8),
// 여기서는 목표값을 그대로 개루프 PWM으로 변환하기만 한다.
constexpr int16_t MAX_DRIVE_SPEED_MMPS = 600;

// tank_drive.ino 배선 실측과 동일: 전진 명령에서 한쪽 바퀴가 반대로 돌면 이
// 값만 바꾼다.
constexpr bool LEFT_MOTOR_REVERSED = false;
constexpr bool RIGHT_MOTOR_REVERSED = true;

int16_t g_appliedPwmLeft = 0;
int16_t g_appliedPwmRight = 0;
bool g_driverEnabled = false;

void writePwm(uint8_t pin, uint8_t channel, uint8_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(pin, duty);
#else
  ledcWrite(channel, duty);
#endif
}

void writeAllPwmOff() {
  writePwm(LEFT_RPWM_PIN, LEFT_RPWM_CHANNEL, 0);
  writePwm(LEFT_LPWM_PIN, LEFT_LPWM_CHANNEL, 0);
  writePwm(RIGHT_RPWM_PIN, RIGHT_RPWM_CHANNEL, 0);
  writePwm(RIGHT_LPWM_PIN, RIGHT_LPWM_CHANNEL, 0);
}

bool isDirectionReversing(int16_t before, int16_t after) {
  return (before > 0 && after < 0) || (before < 0 && after > 0);
}

void writeOneMotor(int16_t command, uint8_t rpwmPin, uint8_t rpwmChannel, uint8_t lpwmPin,
                   uint8_t lpwmChannel) {
  // 두 방향 PWM을 동시에 출력하지 않는다.
  if (command > 0) {
    writePwm(lpwmPin, lpwmChannel, 0);
    writePwm(rpwmPin, rpwmChannel, static_cast<uint8_t>(command));
  } else if (command < 0) {
    writePwm(rpwmPin, rpwmChannel, 0);
    writePwm(lpwmPin, lpwmChannel, static_cast<uint8_t>(-command));
  } else {
    writePwm(rpwmPin, rpwmChannel, 0);
    writePwm(lpwmPin, lpwmChannel, 0);
  }
}

int16_t mmpsToSignedPwm(int16_t targetMmps, bool reversed) {
  const int32_t clamped = constrain(targetMmps, -MAX_DRIVE_SPEED_MMPS, MAX_DRIVE_SPEED_MMPS);
  const int32_t pwm = (clamped * PWM_MAX) / MAX_DRIVE_SPEED_MMPS;
  return static_cast<int16_t>(reversed ? -pwm : pwm);
}

}  // namespace

void motorDriverInit() {
  pinMode(MOTOR_EN_PIN, OUTPUT);
  digitalWrite(MOTOR_EN_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(LEFT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcAttach(LEFT_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcAttach(RIGHT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcAttach(RIGHT_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
#else
  ledcSetup(LEFT_RPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(LEFT_LPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(RIGHT_RPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(RIGHT_LPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);

  ledcAttachPin(LEFT_RPWM_PIN, LEFT_RPWM_CHANNEL);
  ledcAttachPin(LEFT_LPWM_PIN, LEFT_LPWM_CHANNEL);
  ledcAttachPin(RIGHT_RPWM_PIN, RIGHT_RPWM_CHANNEL);
  ledcAttachPin(RIGHT_LPWM_PIN, RIGHT_LPWM_CHANNEL);
#endif

  writeAllPwmOff();
}

void applySafeOutputs() {
  digitalWrite(MOTOR_EN_PIN, LOW);
  writeAllPwmOff();
  g_appliedPwmLeft = 0;
  g_appliedPwmRight = 0;
  g_driverEnabled = false;
}

void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps,
                        int16_t targetSteeringMdeg) {
  // 조향 모터가 캐스터 휠로 대체되어 이 필드는 더 이상 액추에이션에 쓰이지
  // 않는다(프로토콜 호환을 위해 값 자체는 계속 수신·보관한다).
  (void)targetSteeringMdeg;

  const int16_t nextLeft = mmpsToSignedPwm(targetDriveLeftMmps, LEFT_MOTOR_REVERSED);
  const int16_t nextRight = mmpsToSignedPwm(targetDriveRightMmps, RIGHT_MOTOR_REVERSED);

  if (isDirectionReversing(g_appliedPwmLeft, nextLeft) ||
      isDirectionReversing(g_appliedPwmRight, nextRight)) {
    digitalWrite(MOTOR_EN_PIN, LOW);
    writeAllPwmOff();
    delay(DIRECTION_DEAD_TIME_MS);
  }

  writeOneMotor(nextLeft, LEFT_RPWM_PIN, LEFT_RPWM_CHANNEL, LEFT_LPWM_PIN, LEFT_LPWM_CHANNEL);
  writeOneMotor(nextRight, RIGHT_RPWM_PIN, RIGHT_RPWM_CHANNEL, RIGHT_LPWM_PIN, RIGHT_LPWM_CHANNEL);

  g_appliedPwmLeft = nextLeft;
  g_appliedPwmRight = nextRight;
  g_driverEnabled = (nextLeft != 0 || nextRight != 0);
  digitalWrite(MOTOR_EN_PIN, g_driverEnabled ? HIGH : LOW);
}

bool motorDriverEnabled() {
  return g_driverEnabled;
}

int16_t motorDriverAppliedPwmLeft() {
  return g_appliedPwmLeft;
}

int16_t motorDriverAppliedPwmRight() {
  return g_appliedPwmRight;
}

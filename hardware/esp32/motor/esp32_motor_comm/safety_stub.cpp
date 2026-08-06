#include "safety_stub.h"

#include <Arduino.h>
#include <esp_arduino_version.h>

// 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
// 추가하지 말 것(README 참고).

namespace {

// tank_drive.ino(S15P11A301-195)와 esp32_vehicle_control 벤치 시험에서 실측·검증한
// 배선과 동일하다. 후륜 좌·우 구동 BTS7960 2개이며, 전륜 조향은 모터가 아니라
// 서보이므로 이 파일에 핀이 없다(steering.cpp의 GPIO18).
constexpr uint8_t LEFT_RPWM_PIN = 25;
constexpr uint8_t LEFT_LPWM_PIN = 26;
constexpr uint8_t RIGHT_RPWM_PIN = 32;
constexpr uint8_t RIGHT_LPWM_PIN = 33;

// 두 주행 드라이버의 R_EN, L_EN을 함께 제어하는 공통 Enable.
constexpr uint8_t MOTOR_EN_PIN = 23;

// Arduino-ESP32 Core 2.x에서만 사용하는 LEDC 채널 번호. 조향 서보는 다른 타이머의
// 채널 4를 쓴다(steering.cpp) - 20kHz와 50Hz가 타이머를 공유하면 안 된다(§34-1).
constexpr uint8_t LEFT_RPWM_CHANNEL = 0;
constexpr uint8_t LEFT_LPWM_CHANNEL = 1;
constexpr uint8_t RIGHT_RPWM_CHANNEL = 2;
constexpr uint8_t RIGHT_LPWM_CHANNEL = 3;

constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr int16_t PWM_MAX = 255;

// 방향 반전 시 출력을 끈 상태로 유지하는 시간. esp32_vehicle_control 벤치 시험에서
// RS540 2개 + 12V 유아전동차 배터리 조합으로 검증한 값이며, 관성이 큰 차체에서
// 이보다 짧으면 드라이버가 역기전력을 맞는다. 최종값은 §35-4에서 확정한다.
constexpr uint32_t DIRECTION_CHANGE_STOP_MS = 500;

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

// 출력이 0을 거쳐 반대 방향으로 가는 경로에서도 데드타임을 적용하기 위해 마지막
// **실제 구동** 방향과 정지 시각을 따로 기억한다. 이것이 없으면 전진 → 0 → 후진
// 순서로 오는 명령이 데드타임을 그냥 통과한다.
int16_t g_lastDrivenPwmLeft = 0;
int16_t g_lastDrivenPwmRight = 0;
uint32_t g_driveStoppedAtMs = 0;

// 방향 반전 대기 상태. 대기 중에는 출력이 0이고 최신 목표만 갱신된다.
bool g_directionChangePending = false;
int16_t g_pendingPwmLeft = 0;
int16_t g_pendingPwmRight = 0;
uint32_t g_directionChangeStartedMs = 0;

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

void writeDriveNow(int16_t nextLeft, int16_t nextRight) {
  // **정지 시각은 「굴리다 섰다」에만 찍는다** (S15P11A301-298).
  //
  // 종전에는 양측이 0 이기만 하면 매번 갱신했다. control_task 가 100Hz 로
  // applyDriveTargets(0,0) 을 호출하면 이 타임스탬프가 계속 새로고침되어 아래
  // "정지 후 역방향" 데드타임이 **영구히 만료되지 않고 역전이 무한 지연**된다.
  // 젯슨이 0 을 스트리밍할 때마다 50Hz 로 이미 벌어지던 잠재 버그이고, 모든
  // 액추에이션이 100Hz 루프로 옮겨 오면서 간헐이 확정이 된다. CTRL-35 가 회귀
  // 가드다(대기 후 역전이 실제로 일어나는지 확인한다).
  const bool wasDriving = g_appliedPwmLeft != 0 || g_appliedPwmRight != 0;

  writeOneMotor(nextLeft, LEFT_RPWM_PIN, LEFT_RPWM_CHANNEL, LEFT_LPWM_PIN, LEFT_LPWM_CHANNEL);
  writeOneMotor(nextRight, RIGHT_RPWM_PIN, RIGHT_RPWM_CHANNEL, RIGHT_LPWM_PIN, RIGHT_LPWM_CHANNEL);

  g_appliedPwmLeft = nextLeft;
  g_appliedPwmRight = nextRight;
  if (nextLeft != 0) g_lastDrivenPwmLeft = nextLeft;
  if (nextRight != 0) g_lastDrivenPwmRight = nextRight;
  if (nextLeft == 0 && nextRight == 0 && wasDriving) g_driveStoppedAtMs = millis();
  g_driverEnabled = (nextLeft != 0 || nextRight != 0);
  digitalWrite(MOTOR_EN_PIN, g_driverEnabled ? HIGH : LOW);
}

void holdDriveOutputsOff() {
  const bool wasDriving = g_appliedPwmLeft != 0 || g_appliedPwmRight != 0;
  digitalWrite(MOTOR_EN_PIN, LOW);
  writeAllPwmOff();
  g_appliedPwmLeft = 0;
  g_appliedPwmRight = 0;
  g_driverEnabled = false;
  if (wasDriving) g_driveStoppedAtMs = millis();
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
  holdDriveOutputsOff();
  g_directionChangePending = false;
  g_pendingPwmLeft = 0;
  g_pendingPwmRight = 0;
  // 조향각은 건드리지 않는다. 정지가 곧 정차가 아니므로 관성 주행 중 중립으로
  // 꺾으면 의도한 궤적을 벗어난다(§34-7 「정지 시 조향각은 유지한다」).
}

void applyDriveTargets(int16_t targetDriveLeftMmps, int16_t targetDriveRightMmps) {
  int16_t nextLeft = mmpsToSignedPwm(targetDriveLeftMmps, LEFT_MOTOR_REVERSED);
  int16_t nextRight = mmpsToSignedPwm(targetDriveRightMmps, RIGHT_MOTOR_REVERSED);

  // 전륜 조향 차량에서 좌·우 반대 방향 구동은 조향 링크와 다투는 명령이다.
  // Jetson은 좌·우에 같은 값을 보내므로(§34-2) 정상 경로에서는 발생하지 않는다.
  const bool leftForward = targetDriveLeftMmps > 0;
  const bool rightForward = targetDriveRightMmps > 0;
  if (targetDriveLeftMmps != 0 && targetDriveRightMmps != 0 && leftForward != rightForward) {
    nextLeft = 0;
    nextRight = 0;
  }

  if (g_directionChangePending) {
    // 대기 중에는 최신 목표만 갱신한다. 반전 방향이 또 바뀌면 정지 시간을 다시
    // 시작해 데드타임을 우회하지 못하게 한다.
    if (isDirectionReversing(g_pendingPwmLeft, nextLeft) ||
        isDirectionReversing(g_pendingPwmRight, nextRight)) {
      g_directionChangeStartedMs = millis();
    }
    g_pendingPwmLeft = nextLeft;
    g_pendingPwmRight = nextRight;
    holdDriveOutputsOff();
    return;
  }

  const uint32_t nowMs = millis();
  if (isDirectionReversing(g_appliedPwmLeft, nextLeft) ||
      isDirectionReversing(g_appliedPwmRight, nextRight)) {
    holdDriveOutputsOff();
    g_pendingPwmLeft = nextLeft;
    g_pendingPwmRight = nextRight;
    g_directionChangeStartedMs = nowMs;
    g_directionChangePending = true;
    return;
  }

  // 이미 출력이 0인 상태라도 마지막 실제 구동 방향을 기준으로 남은 데드타임을
  // 적용한다(정지를 거쳐 반전하는 경로).
  const bool outputsAreOff = g_appliedPwmLeft == 0 && g_appliedPwmRight == 0;
  const bool reversingLastDriven = isDirectionReversing(g_lastDrivenPwmLeft, nextLeft) ||
                                   isDirectionReversing(g_lastDrivenPwmRight, nextRight);
  if (outputsAreOff && reversingLastDriven &&
      nowMs - g_driveStoppedAtMs < DIRECTION_CHANGE_STOP_MS) {
    g_pendingPwmLeft = nextLeft;
    g_pendingPwmRight = nextRight;
    g_directionChangeStartedMs = g_driveStoppedAtMs;
    g_directionChangePending = true;
    return;
  }

  writeDriveNow(nextLeft, nextRight);
}

void driveUpdate(uint32_t nowMs) {
  if (!g_directionChangePending) return;
  if (nowMs - g_directionChangeStartedMs < DIRECTION_CHANGE_STOP_MS) return;

  const int16_t nextLeft = g_pendingPwmLeft;
  const int16_t nextRight = g_pendingPwmRight;
  g_directionChangePending = false;
  g_pendingPwmLeft = 0;
  g_pendingPwmRight = 0;
  writeDriveNow(nextLeft, nextRight);
}

bool driveDirectionChangePending() {
  return g_directionChangePending;
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

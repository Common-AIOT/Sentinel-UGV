#include "steering.h"

#include <Arduino.h>
#include <esp_arduino_version.h>
#include <math.h>

#include "steering_limits.h"

// 주의: 이 UART는 바이너리 프로토콜 전용이므로 여기에 Serial.print() 디버그를
// 추가하지 말 것(README 참고).

namespace {

// ---- 배선 ----
// DS51150 신호선. 부팅 strapping 핀과 부팅 중 상태가 변하는 핀을 피해야 하며
// (§34-1) 외부 pull-down을 함께 둔다(§34-10). GPIO18은 esp32_vehicle_control 벤치
// 시험(조향 서보 실동작 확인)에서 쓴 것과 같은 핀이다.
constexpr uint8_t SERVO_SIGNAL_PIN = 18;

// ---- 서보 신호 규격 (표준 RC PWM, 데이터시트 확인은 TBD-HW-008) ----
constexpr uint32_t SERVO_PWM_FREQUENCY_HZ = 50;
constexpr uint8_t SERVO_PWM_RESOLUTION_BITS = 16;
constexpr uint32_t SERVO_PWM_MAX_DUTY = (1UL << SERVO_PWM_RESOLUTION_BITS) - 1UL;
constexpr uint32_t SERVO_PWM_PERIOD_US = 1000000UL / SERVO_PWM_FREQUENCY_HZ;
constexpr float SERVO_MIN_PULSE_US = 500.0f;
constexpr float SERVO_MAX_PULSE_US = 2500.0f;
constexpr float SERVO_TOTAL_ANGLE_DEG = 270.0f;

// 구동 PWM(20kHz)과 같은 LEDC 타이머를 쓰면 한쪽 주파수를 바꿀 때 다른 쪽이 함께
// 흔들린다(§34-1). Core 2.x는 채널 2개가 타이머 1개를 공유하므로 구동이 쓰는
// 0~3(타이머 0·1) 밖의 채널 4(타이머 2)를 배정한다. Core 3.x는 핀으로 제어하며
// ledcAttach가 빈 타이머를 알아서 잡는다.
constexpr uint8_t SERVO_PWM_CHANNEL = 4;

// ---- 캘리브레이션 (§35-3 「조향 중립·각도 매핑」 실측 전 임시값) ----
// 서보 각도계의 중립과 좌·우 엔드포인트. 벤치 시험에서 링키지 기계 한계보다
// 안쪽임을 확인한 값이며, 실측 표가 나오면 이 두 기본값을 굳힌다.
//
// S15P11A301-312 부터 **런타임에 조정할 수 있다**(steeringApplyCalibration).
// 부팅값은 여전히 아래 상수이고, 재부팅하면 반드시 여기로 돌아온다.
constexpr float SERVO_CENTER_DEG = 145.0f;
// **55° 는 실측이다** (2026-08-07, S15P11A301-341) — 중립 145° 에서 좌우 ±55°
// (90~200°)가 링키지 기계 한계 안의 실사용 범위이고, 그때 앞바퀴가 22° 꺾인다.
// 즉 링키지 비 = 55/22 = 2.5 (서보 2.5도당 바퀴 1도). 종전 30 은 벤치 가정값이라
// 게인이 30/30 = 1:1 이 되어 바퀴가 지령의 40%만 꺾였다.
constexpr float SERVO_MAX_OFFSET_DEG = 55.0f;

float g_centerDeg = SERVO_CENTER_DEG;
float g_maxOffsetDeg = SERVO_MAX_OFFSET_DEG;

// 리셋·펌웨어 업로드 직후에는 반드시 펄스를 끊은 상태로 시작한다. GPIO18의 외부
// 10kΩ pull-down이 setup() 전 구간을 LOW로 잡고, 여기의 false가 LEDC attach 이후
// control_task가 명시적으로 활성화할 때까지 듀티 0을 보장한다. 종료 전과 같은 중립
// 펄스(기본 145°)를 첫 유효 출력으로 내보내 중간 각도를 거치지 않는다.
bool g_armed = false;

// STEERING_MAX_MDEG 는 steering_limits.h 에 있다. 수동 채널의 ang 매핑이 같은 값을
// 봐야 하므로 분리했다 - 갈라지면 폰 슬라이더 끝과 실제 δ_max 가 어긋난다.

// +δ = 좌회전(반시계, REP-103). 서보 각도를 올렸을 때 앞바퀴가 우로 꺾이면 이
// 값만 -1로 바꾼다 - CTRL-24 스윕 시험에서 가장 먼저 확인할 항목이다.
constexpr float SERVO_DIRECTION_SIGN = 1.0f;

// δ(도) → 서보 각도(도) 게인. 개루프이므로 이 선형 근사의 오차가 곧 조향 오차다.
// 5점 실측 표(§35-3)가 나오면 선형 보간으로 교체한다.
//
// 오프셋이 런타임에 바뀌면 게인도 같이 바뀐다. **δ_max 자체는 바뀌지 않는다** -
// 프로토콜 상한(STEERING_MAX_MDEG)은 젯슨 vehicle_kinematics 와 맞춰 둔 값이라
// 폰에서 흔들면 안 된다. 바뀌는 것은 그 δ_max 가 몇 도의 서보 회전으로 나가느냐다.
float servoDegPerSteeringDeg() {
  return g_maxOffsetDeg / (STEERING_MAX_MDEG / 1000.0f);
}

// ---- 정지 중 조향 금지 (§34-2) ----
// 임계 STEERING_MIN_DRIVE_MMPS 도 steering_limits.h 에 있다. 수동 경로가 그 값을
// 모르면 여기서 거부될 명령을 계속 보내게 되고, 그 거부는 bit 14 로 올라간다.
//
// 밀리도 반올림 잡음을 회두 명령으로 오판하지 않기 위한 여유.
constexpr int16_t STEERING_STATIONARY_TOLERANCE_MDEG = 200;

// 슬루레이트 갱신 간격. 서보 신호 자체가 50Hz라 이보다 자주 써도 의미가 없다(§34-8).
constexpr uint32_t SERVO_UPDATE_INTERVAL_MS = 20;

int16_t g_commandedMdeg = 0;  // 클램프까지 끝난 최종 목표
int16_t g_outputMdeg = 0;     // 슬루레이트 제한을 거쳐 실제 추종 중인 목표
int16_t g_actuatorUs = 0;
uint16_t g_maxRateMdps = 0;   // 0 = 소프트웨어 제한 없음(§34-5 프로토콜 정의)
uint32_t g_lastUpdateMs = 0;

void writePwm(uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(SERVO_SIGNAL_PIN, duty);
#else
  ledcWrite(SERVO_PWM_CHANNEL, duty);
#endif
}

float steeringMdegToServoDeg(int16_t steeringMdeg) {
  const float servoDeg =
      g_centerDeg +
      SERVO_DIRECTION_SIGN * (steeringMdeg / 1000.0f) * servoDegPerSteeringDeg();
  // 최종 출력 직전에도 엔드포인트를 다시 제한한다. 어떤 코드 경로로 들어와도
  // 서보가 링키지의 기계 한계를 밀지 않게 하는 마지막 방어선이다(§21.6).
  return constrain(servoDeg, g_centerDeg - g_maxOffsetDeg, g_centerDeg + g_maxOffsetDeg);
}

float servoDegToPulseUs(float servoDeg) {
  const float bounded = constrain(servoDeg, 0.0f, SERVO_TOTAL_ANGLE_DEG);
  return SERVO_MIN_PULSE_US +
         bounded / SERVO_TOTAL_ANGLE_DEG * (SERVO_MAX_PULSE_US - SERVO_MIN_PULSE_US);
}

uint32_t pulseUsToDuty(float pulseUs) {
  const float bounded = constrain(pulseUs, SERVO_MIN_PULSE_US, SERVO_MAX_PULSE_US);
  return (uint32_t)(bounded * (float)SERVO_PWM_MAX_DUTY / (float)SERVO_PWM_PERIOD_US + 0.5f);
}

void writeSteeringMdeg(int16_t steeringMdeg) {
  // 출력이 꺼져 있으면 듀티 0 으로 펄스를 끊는다. 서보는 토크를 잃고 free 가 된다.
  // 목표(g_commandedMdeg/g_outputMdeg)는 그대로 두어 다시 켤 때 튀지 않게 한다.
  if (!g_armed) {
    writePwm(0);
    g_actuatorUs = 0;
    return;
  }
  const float pulseUs = servoDegToPulseUs(steeringMdegToServoDeg(steeringMdeg));
  writePwm(pulseUsToDuty(pulseUs));
  g_actuatorUs = (int16_t)lroundf(pulseUs);
}

}  // namespace

void steeringInit() {
  // 부팅 구간에 유효한 펄스가 만들어지지 않게 먼저 LOW로 고정한 뒤 attach한다
  // (§34-10, 외부 pull-down과 이중 방어).
  pinMode(SERVO_SIGNAL_PIN, OUTPUT);
  digitalWrite(SERVO_SIGNAL_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(SERVO_SIGNAL_PIN, SERVO_PWM_FREQUENCY_HZ, SERVO_PWM_RESOLUTION_BITS);
#else
  ledcSetup(SERVO_PWM_CHANNEL, SERVO_PWM_FREQUENCY_HZ, SERVO_PWM_RESOLUTION_BITS);
  ledcAttachPin(SERVO_SIGNAL_PIN, SERVO_PWM_CHANNEL);
#endif

  // 재부팅 직후 내부 목표만 중립으로 준비한다. 여기서는 유효한 RC 펄스를 내보내지
  // 않는다. setup()이 주변장치 초기화를 끝낸 뒤 공유 상태를 arm하고 control_task의
  // 첫 steeringUpdate()가 중간값 없이 이 중립을 첫 펄스로 출력한다.
  g_commandedMdeg = 0;
  g_outputMdeg = 0;
  g_maxRateMdps = 0;
  g_lastUpdateMs = millis();
  writePwm(0);
  g_actuatorUs = 0;
}

bool steeringSetTarget(int16_t requestedMdeg, uint16_t maxRateMdps, int16_t driveTargetMmps) {
  g_maxRateMdps = maxRateMdps;

  const int16_t clamped =
      (int16_t)constrain((int32_t)requestedMdeg, -(int32_t)STEERING_MAX_MDEG,
                          (int32_t)STEERING_MAX_MDEG);

  // 정지 상태에서의 조향 변경 거부. 목표를 바꾸지 않고 마지막 값을 유지한다.
  if (abs(driveTargetMmps) < STEERING_MIN_DRIVE_MMPS &&
      abs(clamped - g_commandedMdeg) > STEERING_STATIONARY_TOLERANCE_MDEG) {
    return false;
  }

  g_commandedMdeg = clamped;
  return clamped == requestedMdeg;
}

void steeringUpdate(uint32_t nowMs) {
  const uint32_t elapsedMs = nowMs - g_lastUpdateMs;
  if (elapsedMs < SERVO_UPDATE_INTERVAL_MS) return;
  g_lastUpdateMs = nowMs;

  if (g_outputMdeg != g_commandedMdeg) {
    if (g_maxRateMdps == 0) {
      // 프로토콜상 0은 「제한 없음」이다. 그때 실제 변화율은 서보의 물리 최대
      // 속도이며 그것이 곧 급조향이므로, Jetson은 항상 유효한 값을 보낸다
      // (vehicle_kinematics의 max_steering_rate_mdps, §35-4로 실측 확정).
      g_outputMdeg = g_commandedMdeg;
    } else {
      // 태스크 지연으로 elapsed가 크게 튀었을 때 한 번에 몰아서 꺾이지 않게
      // 상한을 둔다 - 슬루레이트 제한의 목적 자체가 계단 입력 방지다.
      const uint32_t boundedElapsedMs = min(elapsedMs, 100UL);
      const int32_t maxStepMdeg =
          (int32_t)(((uint64_t)g_maxRateMdps * boundedElapsedMs) / 1000ULL);
      const int32_t difference = (int32_t)g_commandedMdeg - (int32_t)g_outputMdeg;
      if (abs(difference) <= maxStepMdeg) {
        g_outputMdeg = g_commandedMdeg;
      } else {
        g_outputMdeg += (int16_t)(difference > 0 ? maxStepMdeg : -maxStepMdeg);
      }
    }
  }

  // 목표에 도달한 뒤에도 매 주기 같은 듀티를 다시 쓴다. LEDC는 듀티를 유지하므로
  // 기능상 필요는 없지만, 서보 지령이 살아 있다는 것을 파형으로 확인할 수 있다.
  writeSteeringMdeg(g_outputMdeg);
}

int16_t steeringTargetMdeg() {
  return g_outputMdeg;
}

int16_t steeringActuatorCmdUs() {
  return g_actuatorUs;
}

int16_t steeringMaxMdeg() {
  return STEERING_MAX_MDEG;
}

void steeringApplyCalibration(uint8_t centerDeg, uint8_t maxOffsetDeg, bool armed) {
  // 인자를 믿지 않는다. manual_web 이 이미 검사하지만 최종 방어선은 여기다(§21.6).
  const float offset = constrain((float)maxOffsetDeg, 0.0f,
                                 (float)STEERING_OFFSET_HARD_MAX_DEG);
  const float center = constrain((float)centerDeg,
                                 (float)STEERING_CENTER_HARD_MIN_DEG,
                                 (float)STEERING_CENTER_HARD_MAX_DEG);

  // 중립±오프셋이 서보 물리 범위(0~270°)를 넘지 않게 오프셋을 줄인다. 중립을
  // 옮기지 않는 것은 의도다 - 사람이 방금 맞춘 중립이 조용히 이동하면 그게 더 나쁘다.
  const float headroom = min(center, SERVO_TOTAL_ANGLE_DEG - center);
  g_maxOffsetDeg = min(offset, headroom);
  g_centerDeg = center;
  g_armed = armed;
  // 듀티는 다음 steeringUpdate(최대 20ms 뒤)가 새 매핑으로 다시 쓴다.
}

void steeringJogToMdeg(int16_t mdeg) {
  // §34-2 우회 지점. steeringSetTarget 을 부르지 않는 이유가 이것 하나다.
  g_commandedMdeg =
      (int16_t)constrain((int32_t)mdeg, -(int32_t)STEERING_MAX_MDEG,
                         (int32_t)STEERING_MAX_MDEG);
}

// 전륜 조향 서보(DS51150-12V) 벤치 시험 스케치 (S15P11A301-297).
//
// 목적은 둘이다.
//   1. CTRL-24 조향 스윕 — 중립 → 좌 δ_max → 중립 → 우 δ_max → 중립을 앞바퀴를
//      띄운 상태에서 돌려 링키지 간섭·기계 한계 접촉·좌우 스트로크 대칭을 본다.
//   2. §35-3 「조향 중립·각도 매핑」 — 지령 펄스폭을 1µs 단위로 움직여 앞바퀴가
//      차체 종축과 평행한 값(중립)과 기계 한계 직전의 좌·우 엔드포인트를 찾는다.
//
// 여기서 얻은 값을 `esp32_motor_comm/steering.cpp`의 SERVO_CENTER_DEG /
// SERVO_MAX_OFFSET_DEG / STEERING_MAX_MDEG에 넣는다. **서보 지령은 펄스폭(µs)으로
// 기록한다** — 각도 단위로 적으면 라이브러리·펌웨어 버전에 따라 매핑이 바뀐다(§35-3).
//
// 이 스케치는 벤치 전용이며 제어 경로가 아니다. Wi-Fi/Bluetooth를 쓰지 않고
// (§34-1: 제어 펌웨어에서 무선 비활성) USB 시리얼 명령으로만 조작한다. 구동 모터는
// 건드리지 않는다 — 조향 시험은 앞바퀴를 띄운 상태에서 조향만 움직여야 한다(§21.4).
//
// 시리얼 명령 (115200bps, 한 줄에 하나):
//   c            중립(현재 CENTER_US)으로
//   l / r        좌 / 우 엔드포인트로
//   + / -        현재 펄스폭 STEP_US만큼 증가 / 감소
//   u <us>       펄스폭 직접 지정 (예: u 1500)
//   d <deg>      서보 각도 지정 (예: d 145.0)
//   s            중립→좌→중립→우→중립 스윕 1회 (CTRL-24)
//   o            서보 신호 끄기(무여자, 링키지 유격 측정용)
//   ?            현재 상태 출력
#include <Arduino.h>
#include <esp_arduino_version.h>
#include <math.h>

// esp32_motor_comm/steering.cpp와 같은 핀·신호 규격을 쓴다. 두 곳이 갈라지면
// 벤치에서 잡은 값이 펌웨어에서 재현되지 않는다.
constexpr uint8_t SERVO_SIGNAL_PIN = 18;
constexpr uint32_t SERVO_PWM_FREQUENCY_HZ = 50;
constexpr uint8_t SERVO_PWM_RESOLUTION_BITS = 16;
constexpr uint32_t SERVO_PWM_MAX_DUTY = (1UL << SERVO_PWM_RESOLUTION_BITS) - 1UL;
constexpr uint32_t SERVO_PWM_PERIOD_US = 1000000UL / SERVO_PWM_FREQUENCY_HZ;
constexpr uint8_t SERVO_PWM_CHANNEL = 4;  // Core 2.x 전용(타이머 2)

constexpr float SERVO_MIN_PULSE_US = 500.0f;
constexpr float SERVO_MAX_PULSE_US = 2500.0f;
constexpr float SERVO_TOTAL_ANGLE_DEG = 270.0f;

// 실측 전 임시값. 이 세 값을 찾는 것이 이 스케치의 목적이다.
constexpr float CENTER_DEG = 145.0f;
constexpr float MAX_OFFSET_DEG = 30.0f;

// 안전 한계: 어떤 명령도 이 범위를 벗어나지 못한다. 링키지 기계 한계보다 안쪽에
// 두고, 넓혀야 한다면 이 두 상수를 의식적으로 바꾼다.
constexpr float SAFE_MIN_DEG = CENTER_DEG - MAX_OFFSET_DEG;
constexpr float SAFE_MAX_DEG = CENTER_DEG + MAX_OFFSET_DEG;

constexpr float STEP_US = 5.0f;
// 스윕 속도. 급조향은 배터리 전압 강하가 커서 벤치에서도 완만하게 움직인다(§35-4).
constexpr float SWEEP_DEG_PER_SECOND = 40.0f;
constexpr uint32_t SWEEP_INTERVAL_MS = 20;

float g_currentDeg = CENTER_DEG;
bool g_signalEnabled = true;

float degToPulseUs(float deg) {
  const float bounded = constrain(deg, 0.0f, SERVO_TOTAL_ANGLE_DEG);
  return SERVO_MIN_PULSE_US +
         bounded / SERVO_TOTAL_ANGLE_DEG * (SERVO_MAX_PULSE_US - SERVO_MIN_PULSE_US);
}

float pulseUsToDeg(float pulseUs) {
  const float bounded = constrain(pulseUs, SERVO_MIN_PULSE_US, SERVO_MAX_PULSE_US);
  return (bounded - SERVO_MIN_PULSE_US) / (SERVO_MAX_PULSE_US - SERVO_MIN_PULSE_US) *
         SERVO_TOTAL_ANGLE_DEG;
}

void writePwm(uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(SERVO_SIGNAL_PIN, duty);
#else
  ledcWrite(SERVO_PWM_CHANNEL, duty);
#endif
}

void writeDeg(float deg) {
  g_currentDeg = constrain(deg, SAFE_MIN_DEG, SAFE_MAX_DEG);
  if (!g_signalEnabled) return;
  const float pulseUs = degToPulseUs(g_currentDeg);
  writePwm((uint32_t)(pulseUs * (float)SERVO_PWM_MAX_DUTY / (float)SERVO_PWM_PERIOD_US + 0.5f));
}

void printState() {
  Serial.printf("[STATE] %s  angle=%.2f deg  pulse=%.0f us  (safe %.1f~%.1f deg, center %.1f)\n",
                g_signalEnabled ? "ON " : "OFF", g_currentDeg, degToPulseUs(g_currentDeg),
                SAFE_MIN_DEG, SAFE_MAX_DEG, CENTER_DEG);
}

void enableSignal() {
  g_signalEnabled = true;
  writeDeg(g_currentDeg);
}

void disableSignal() {
  // 듀티 0은 유효한 RC 펄스가 아니므로 서보가 무여자된다. 링키지 유격(히스테리시스)
  // 측정 때 손으로 바퀴를 움직여 보기 위한 상태다.
  g_signalEnabled = false;
  writePwm(0);
  Serial.println("[SERVO] 신호 끔 — 서보 무여자. 링키지를 손으로 움직여도 된다");
}

// 완만하게 목표 각도까지 이동한다. 벤치이므로 블로킹으로 두고, 이동 중 시리얼
// 입력은 받지 않는다.
void slewTo(float targetDeg) {
  const float bounded = constrain(targetDeg, SAFE_MIN_DEG, SAFE_MAX_DEG);
  const float step = SWEEP_DEG_PER_SECOND * SWEEP_INTERVAL_MS / 1000.0f;
  while (fabsf(bounded - g_currentDeg) > step) {
    writeDeg(g_currentDeg + (bounded > g_currentDeg ? step : -step));
    delay(SWEEP_INTERVAL_MS);
  }
  writeDeg(bounded);
  delay(SWEEP_INTERVAL_MS);
}

void runSweep() {
  Serial.println("[SWEEP] CTRL-24: 중립 → 좌 → 중립 → 우 → 중립");
  enableSignal();
  slewTo(CENTER_DEG);
  delay(500);
  slewTo(SAFE_MAX_DEG);  // +δ = 좌회전(반시계). 실제 방향이 반대면 펌웨어의
  delay(800);            // SERVO_DIRECTION_SIGN을 -1로 바꾼다.
  slewTo(CENTER_DEG);
  delay(500);
  slewTo(SAFE_MIN_DEG);
  delay(800);
  slewTo(CENTER_DEG);
  Serial.println("[SWEEP] 완료. 링키지 간섭·기계 한계 접촉·좌우 스트로크 대칭을 기록하라");
  printState();
}

void printHelp() {
  Serial.println();
  Serial.println("=== 조향 서보 벤치 시험 (앞바퀴를 띄운 상태에서만 사용) ===");
  Serial.println("  c        중립");
  Serial.println("  l / r    좌 / 우 엔드포인트");
  Serial.println("  + / -    펄스폭 5us 증감");
  Serial.println("  u <us>   펄스폭 직접 지정");
  Serial.println("  d <deg>  서보 각도 지정");
  Serial.println("  s        스윕 1회 (CTRL-24)");
  Serial.println("  o        신호 끄기(무여자)");
  Serial.println("  ?        상태 출력");
  printState();
}

void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  const char command = line.charAt(0);
  const String argument = line.substring(1);

  switch (command) {
    case 'c':
      enableSignal();
      slewTo(CENTER_DEG);
      break;
    case 'l':
      enableSignal();
      slewTo(SAFE_MAX_DEG);
      break;
    case 'r':
      enableSignal();
      slewTo(SAFE_MIN_DEG);
      break;
    case '+':
      enableSignal();
      writeDeg(pulseUsToDeg(degToPulseUs(g_currentDeg) + STEP_US));
      break;
    case '-':
      enableSignal();
      writeDeg(pulseUsToDeg(degToPulseUs(g_currentDeg) - STEP_US));
      break;
    case 'u':
      enableSignal();
      writeDeg(pulseUsToDeg(argument.toFloat()));
      break;
    case 'd':
      enableSignal();
      writeDeg(argument.toFloat());
      break;
    case 's':
      runSweep();
      return;
    case 'o':
      disableSignal();
      return;
    case '?':
      printHelp();
      return;
    default:
      Serial.println("[WARN] 모르는 명령이다. ? 로 도움말을 본다");
      return;
  }
  printState();
}

void setup() {
  Serial.begin(115200);
  delay(300);

  // 부팅 구간에 유효한 펄스가 만들어지지 않게 먼저 LOW로 고정한다(§34-10).
  pinMode(SERVO_SIGNAL_PIN, OUTPUT);
  digitalWrite(SERVO_SIGNAL_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(SERVO_SIGNAL_PIN, SERVO_PWM_FREQUENCY_HZ, SERVO_PWM_RESOLUTION_BITS);
#else
  ledcSetup(SERVO_PWM_CHANNEL, SERVO_PWM_FREQUENCY_HZ, SERVO_PWM_RESOLUTION_BITS);
  ledcAttachPin(SERVO_SIGNAL_PIN, SERVO_PWM_CHANNEL);
#endif

  writeDeg(CENTER_DEG);
  printHelp();
}

void loop() {
  if (Serial.available() > 0) {
    handleLine(Serial.readStringUntil('\n'));
  }
  delay(5);
}

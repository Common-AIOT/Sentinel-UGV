#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_arduino_version.h>

// 반드시 사용하는 네트워크 정보로 수정한다.
constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char MDNS_HOSTNAME[] = "esp32-dual-motor";

// ESP32 DevKit + BTS7960(IBT-2) 3개 (전후진 좌/우 + 조향)
constexpr uint8_t LEFT_RPWM_PIN = 25;
constexpr uint8_t LEFT_LPWM_PIN = 26;
constexpr uint8_t RIGHT_RPWM_PIN = 32;
constexpr uint8_t RIGHT_LPWM_PIN = 33;

// 두 주행 드라이버의 R_EN, L_EN을 함께 제어하는 공통 Enable
constexpr uint8_t MOTOR_EN_PIN = 23;

// 조향 모터(RS380SP-12V) 드라이버. 주행 모터와 Enable을 분리해서
// 조향만 따로 켜고 끌 수 있게 한다.
constexpr uint8_t STEER_RPWM_PIN = 27;
constexpr uint8_t STEER_LPWM_PIN = 14;
constexpr uint8_t STEER_EN_PIN = 13;

// Arduino-ESP32 Core 2.x에서만 사용하는 LEDC 채널 번호
constexpr uint8_t LEFT_RPWM_CHANNEL = 0;
constexpr uint8_t LEFT_LPWM_CHANNEL = 1;
constexpr uint8_t RIGHT_RPWM_CHANNEL = 2;
constexpr uint8_t RIGHT_LPWM_CHANNEL = 3;
constexpr uint8_t STEER_RPWM_CHANNEL = 4;
constexpr uint8_t STEER_LPWM_CHANNEL = 5;

constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr int16_t PWM_MAX = 255;
constexpr uint16_t DIRECTION_DEAD_TIME_MS = 50;

// 브라우저에서 명령이 끊기거나 Wi-Fi가 끊기면 자동 정지한다.
constexpr uint32_t COMMAND_TIMEOUT_MS = 1500;

// 전진 명령에서 한쪽 바퀴가 반대로 돌면 해당 값만 바꾼다.
constexpr bool LEFT_MOTOR_REVERSED = false;
constexpr bool RIGHT_MOTOR_REVERSED = true;

// 조향 배선 후 좌/우가 반대로 움직이면 이 값만 true로 바꾼다.
constexpr bool STEER_MOTOR_REVERSED = false;

// 실측한 조향 임계 스텝. 중앙을 0으로 두고 좌/우 각각 6스텝에서 기구적 한계에
// 닿는 것을 확인했다. 좌/우 버튼은 이제 이 끝값까지 한 번에 이동한다.
constexpr int8_t STEER_MAX_STEPS = 6;

constexpr uint8_t STEER_NUDGE_DUTY = 90;          // 0~255, 저속 유지
constexpr uint16_t STEER_NUDGE_DURATION_MS = 150; // 스텝 1개에 해당하는 시간

// 좌/우 버튼을 누르면 조향과 별개로 전후진 모터도 짧게 차동 구동해서
// 회전을 보조한다. 필요 없을 때는 웹 UI의 토글 버튼으로 끌 수 있다.
// 실제 구동 duty(assistDuty)는 웹 UI의 보조 속도 슬라이더로 조절한다.
constexpr uint8_t ASSIST_SPEED_PERCENT_MIN = 20;
constexpr uint8_t ASSIST_SPEED_PERCENT_MAX = 100;
constexpr uint8_t ASSIST_SPEED_PERCENT_DEFAULT = 40;

WebServer server(80);

enum class DriveState {
  STOPPED,
  FORWARD,
  BACKWARD
};

DriveState driveState = DriveState::STOPPED;
int16_t currentLeftCommand = 0;
int16_t currentRightCommand = 0;
uint8_t driveSpeedPercent = 31;
uint8_t driveDuty = 80;
uint32_t lastMotorCommandMs = 0;
bool wifiWasConnected = false;

// 중앙을 0으로 보는 상대 스텝 카운터(-STEER_MAX_STEPS ~ +STEER_MAX_STEPS).
// 조향 초기화 버튼으로 좌측 끝 기준을 다시 맞출 수 있다.
int16_t steerStepCount = 0;

// 좌/우 조향 시 전후진 모터의 보조 구동 여부. 기본은 꺼짐(false)이며
// 웹 UI 토글로 켤 수 있다.
bool driveAssistEnabled = false;
uint8_t assistSpeedPercent = ASSIST_SPEED_PERCENT_DEFAULT;
uint8_t assistDuty = static_cast<uint8_t>(
    (static_cast<uint16_t>(ASSIST_SPEED_PERCENT_DEFAULT) * PWM_MAX + 50) /
    100);

const char CONTROL_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ESP32 좌우 모터 제어</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #111827;
      color: #f9fafb;
      font-family: sans-serif;
    }
    main {
      width: min(92vw, 440px);
      text-align: center;
    }
    h1 { font-size: 1.5rem; }
    p { color: #cbd5e1; }
    button {
      width: 100%;
      min-height: 88px;
      margin: 8px 0;
      border: 0;
      border-radius: 14px;
      color: white;
      font-size: 1.35rem;
      font-weight: 700;
      touch-action: none;
      user-select: none;
    }
    #forward { background: #15803d; }
    #backward { background: #1d4ed8; }
    #stop { background: #b91c1c; }
    #status {
      min-height: 1.5em;
      color: #facc15;
      font-weight: 700;
    }
    .speed {
      margin: 20px 0;
      padding: 16px;
      border-radius: 14px;
      background: #1f2937;
    }
    label {
      display: block;
      margin-bottom: 12px;
      font-weight: 700;
    }
    input[type="range"] { width: 100%; }
    .steer {
      margin: 20px 0;
      padding: 16px;
      border-radius: 14px;
      background: #1f2937;
    }
    .steer h2 {
      font-size: 1.1rem;
      margin-top: 0;
    }
    .steer p {
      font-size: 0.9rem;
    }
    .steer-row {
      display: flex;
      gap: 8px;
    }
    .steer-row button {
      min-height: 72px;
      margin: 0;
    }
    #steerLeft { background: #b45309; }
    #steerRight { background: #0f766e; }
    #steerInit {
      background: #374151;
      min-height: 48px;
      font-size: 1rem;
    }
    #steerCount {
      color: #facc15;
      font-weight: 700;
    }
    .assist {
      margin: 20px 0;
      padding: 16px;
      border-radius: 14px;
      background: #1f2937;
    }
    .assist h2 {
      font-size: 1.1rem;
      margin-top: 0;
    }
    .assist p {
      font-size: 0.9rem;
    }
    #assistToggle {
      background: #4c1d95;
      min-height: 56px;
    }
    #assistToggle.on { background: #6d28d9; }
    .assist label { margin-top: 16px; }
  </style>
</head>
<body>
  <main>
    <h1>좌우 DC 모터 Wi-Fi 제어</h1>
    <p>버튼 또는 키보드 F/B를 누르는 동안 두 모터가 동작합니다.</p>
    <div id="status">정지</div>

    <div class="speed">
      <label for="speed">속도: <span id="speedValue">31</span>%</label>
      <input id="speed" type="range" min="20" max="100" value="31">
    </div>

    <button id="forward">전진 (F)</button>
    <button id="backward">후진 (B)</button>
    <button id="stop">정지 (S)</button>

    <div class="steer">
      <h2>조향 제어</h2>
      <p>좌/우 버튼을 누르면 조향이 측정된 임계 스텝(±6)까지 한 번에
        이동합니다.</p>
      <div class="steer-row">
        <button id="steerLeft">◀ 좌 끝 (←)</button>
        <button id="steerRight">우 끝 ▶ (→)</button>
      </div>
      <button id="steerInit">조향 초기화: 좌측 끝 = -6 (R)</button>
      <p>현재 스텝: <span id="steerCount">0</span>
        (초기화 전 바퀴를 손으로 좌측 끝까지 돌려놓은 뒤 누르세요)</p>
    </div>

    <div class="assist">
      <h2>전후진 모터 조향 보조</h2>
      <p>켜두면 좌/우 조향 시 전후진 모터도 짧게 차동 구동해서
        회전을 보조합니다.</p>
      <button id="assistToggle">조향 보조: OFF (A)</button>
      <label for="assistSpeed">보조 속도: <span id="assistSpeedValue">40</span>%</label>
      <input id="assistSpeed" type="range" min="20" max="100" value="40">
    </div>
  </main>

  <script>
    let repeatTimer = null;
    let activeCommand = null;

    function sendCommand(command) {
      fetch('/' + command, { cache: 'no-store' }).catch(() => {});
    }

    function beginMotion(command, text) {
      if (activeCommand === command) return;
      endMotion(false);
      activeCommand = command;
      document.getElementById('status').textContent = text;
      sendCommand(command);
      repeatTimer = setInterval(() => sendCommand(command), 400);
    }

    function endMotion(sendStop = true) {
      if (repeatTimer !== null) {
        clearInterval(repeatTimer);
        repeatTimer = null;
      }
      activeCommand = null;
      document.getElementById('status').textContent = '정지';
      if (sendStop) sendCommand('s');
    }

    const forward = document.getElementById('forward');
    const backward = document.getElementById('backward');
    const stop = document.getElementById('stop');
    const speed = document.getElementById('speed');
    const speedValue = document.getElementById('speedValue');
    const steerLeft = document.getElementById('steerLeft');
    const steerRight = document.getElementById('steerRight');
    const steerInit = document.getElementById('steerInit');
    const steerCount = document.getElementById('steerCount');
    const assistToggle = document.getElementById('assistToggle');
    const assistSpeed = document.getElementById('assistSpeed');
    const assistSpeedValue = document.getElementById('assistSpeedValue');

    function updateSteerCount(text) {
      const value = parseInt(text, 10);
      if (!Number.isNaN(value)) steerCount.textContent = value;
    }

    function sendSteer(command) {
      fetch('/' + command, { cache: 'no-store' })
        .then(response => response.text())
        .then(updateSteerCount)
        .catch(() => {});
    }

    // 좌/우 이동은 최대 6스텝 분량을 한 번에 이어서 움직이므로 완료까지
    // 시간이 걸린다. 진행 중에는 버튼 연타로 명령이 겹치지 않게 막는다.
    let steerBusy = false;
    function nudgeSteer(command) {
      if (steerBusy) return;
      steerBusy = true;
      fetch('/' + command, { cache: 'no-store' })
        .then(response => response.text())
        .then(text => {
          updateSteerCount(text);
          steerBusy = false;
        })
        .catch(() => { steerBusy = false; });
    }

    steerLeft.addEventListener('click', () => nudgeSteer('steer_left'));
    steerRight.addEventListener('click', () => nudgeSteer('steer_right'));
    steerInit.addEventListener('click', () => sendSteer('steer_init'));

    function setAssistLabel(isOn) {
      assistToggle.textContent = isOn ? '조향 보조: ON (A)' : '조향 보조: OFF (A)';
      assistToggle.classList.toggle('on', isOn);
    }

    assistToggle.addEventListener('click', () => {
      fetch('/assist_toggle', { cache: 'no-store' })
        .then(response => response.text())
        .then(text => setAssistLabel(text.trim() === 'ON'))
        .catch(() => {});
    });

    assistSpeed.addEventListener('input', () => {
      assistSpeedValue.textContent = assistSpeed.value;
    });
    assistSpeed.addEventListener('change', () => {
      fetch('/assist_speed?value=' + assistSpeed.value, { cache: 'no-store' })
        .catch(() => {});
    });

    forward.addEventListener(
      'pointerdown', () => beginMotion('f', '전진'));
    backward.addEventListener(
      'pointerdown', () => beginMotion('b', '후진'));

    for (const button of [forward, backward]) {
      button.addEventListener('pointerup', () => endMotion());
      button.addEventListener('pointercancel', () => endMotion());
      button.addEventListener('pointerleave', () => endMotion());
    }

    stop.addEventListener('click', () => endMotion());

    speed.addEventListener('input', () => {
      speedValue.textContent = speed.value;
    });
    speed.addEventListener('change', () => {
      fetch('/speed?value=' + speed.value, { cache: 'no-store' })
        .catch(() => {});
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft') {
        nudgeSteer('steer_left');
        return;
      }
      if (event.key === 'ArrowRight') {
        nudgeSteer('steer_right');
        return;
      }
      if (event.key === 'r' || event.key === 'R') {
        sendSteer('steer_init');
        return;
      }
      if (event.key === 'a' || event.key === 'A') {
        assistToggle.click();
        return;
      }
      if (event.repeat) return;
      if (event.key === 'f' || event.key === 'F') {
        beginMotion('f', '전진');
      } else if (event.key === 'b' || event.key === 'B') {
        beginMotion('b', '후진');
      } else if (event.key === 's' || event.key === 'S') {
        endMotion();
      }
    });

    document.addEventListener('keyup', event => {
      if ('fFbB'.includes(event.key)) endMotion();
    });

    window.addEventListener('blur', () => endMotion());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) endMotion();
    });
  </script>
</body>
</html>
)rawliteral";

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
  writePwm(STEER_RPWM_PIN, STEER_RPWM_CHANNEL, 0);
  writePwm(STEER_LPWM_PIN, STEER_LPWM_CHANNEL, 0);
}

int16_t applyMotorDirection(int16_t command, bool reversed) {
  command = constrain(command, -PWM_MAX, PWM_MAX);
  return reversed ? -command : command;
}

bool isDirectionReversing(int16_t before, int16_t after) {
  return (before > 0 && after < 0) || (before < 0 && after > 0);
}

void writeOneMotor(int16_t command,
                   uint8_t rpwmPin,
                   uint8_t rpwmChannel,
                   uint8_t lpwmPin,
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

// command 범위: -255(후진 최대) ~ 0(정지) ~ +255(전진 최대)
void setDrive(int16_t leftCommand, int16_t rightCommand) {
  const int16_t nextLeft =
      applyMotorDirection(leftCommand, LEFT_MOTOR_REVERSED);
  const int16_t nextRight =
      applyMotorDirection(rightCommand, RIGHT_MOTOR_REVERSED);

  if (isDirectionReversing(currentLeftCommand, nextLeft) ||
      isDirectionReversing(currentRightCommand, nextRight)) {
    digitalWrite(MOTOR_EN_PIN, LOW);
    writeAllPwmOff();
    delay(DIRECTION_DEAD_TIME_MS);
  }

  writeOneMotor(nextLeft,
                LEFT_RPWM_PIN,
                LEFT_RPWM_CHANNEL,
                LEFT_LPWM_PIN,
                LEFT_LPWM_CHANNEL);
  writeOneMotor(nextRight,
                RIGHT_RPWM_PIN,
                RIGHT_RPWM_CHANNEL,
                RIGHT_LPWM_PIN,
                RIGHT_LPWM_CHANNEL);

  currentLeftCommand = nextLeft;
  currentRightCommand = nextRight;

  digitalWrite(MOTOR_EN_PIN,
               (nextLeft == 0 && nextRight == 0) ? LOW : HIGH);
}

void stopDrive() {
  digitalWrite(MOTOR_EN_PIN, LOW);
  writeAllPwmOff();
  currentLeftCommand = 0;
  currentRightCommand = 0;
  driveState = DriveState::STOPPED;
}

void runForward() {
  setDrive(driveDuty, driveDuty);
  driveState = DriveState::FORWARD;
  lastMotorCommandMs = millis();
}

void runBackward() {
  setDrive(-driveDuty, -driveDuty);
  driveState = DriveState::BACKWARD;
  lastMotorCommandMs = millis();
}

void applySpeedToRunningMotors() {
  if (driveState == DriveState::FORWARD) {
    setDrive(driveDuty, driveDuty);
    lastMotorCommandMs = millis();
  } else if (driveState == DriveState::BACKWARD) {
    setDrive(-driveDuty, -driveDuty);
    lastMotorCommandMs = millis();
  }
}

int8_t clampSteerStep(int16_t step) {
  if (step > STEER_MAX_STEPS) return STEER_MAX_STEPS;
  if (step < -STEER_MAX_STEPS) return -STEER_MAX_STEPS;
  return static_cast<int8_t>(step);
}

// 조향 방향(direction)에 맞춰 전후진 모터를 차동 구동해 회전을 보조한다.
// 원래 주행 상태(정지/전진/후진)는 endDriveAssist에서 복원한다.
// 좌회전(direction=-1) 시 왼쪽 바퀴 후진 + 오른쪽 바퀴 전진으로 가정했다.
// 실제로 반대 방향으로 돌면 leftRaw/rightRaw의 부호를 서로 바꿔서 확인한다.
void beginDriveAssist(int8_t direction) {
  const int16_t leftRaw = static_cast<int16_t>(direction) * assistDuty;
  const int16_t rightRaw = static_cast<int16_t>(-direction) * assistDuty;
  setDrive(leftRaw, rightRaw);
  lastMotorCommandMs = millis();
}

void endDriveAssist() {
  if (driveState == DriveState::FORWARD) {
    setDrive(driveDuty, driveDuty);
  } else if (driveState == DriveState::BACKWARD) {
    setDrive(-driveDuty, -driveDuty);
  } else {
    stopDrive();
  }
  lastMotorCommandMs = millis();
}

// 현재 스텝에서 targetStep까지 끊지 않고 한 번의 연속 동작으로 이동한다.
// 좌/우 버튼이 이 함수를 호출해서 측정된 임계 스텝(-6 또는 +6)까지
// 조향을 곧장 이동시킨다.
void moveSteeringToStep(int8_t targetStep) {
  const int8_t clampedTarget = clampSteerStep(targetStep);
  const int16_t delta = clampedTarget - steerStepCount;
  if (delta == 0) return;

  const int8_t direction = (delta > 0) ? 1 : -1;
  const uint8_t stepsToMove =
      static_cast<uint8_t>(delta > 0 ? delta : -delta);
  const uint32_t moveDurationMs =
      static_cast<uint32_t>(stepsToMove) * STEER_NUDGE_DURATION_MS;

  const int16_t rawCommand =
      (direction > 0) ? STEER_NUDGE_DUTY : -STEER_NUDGE_DUTY;
  const int16_t command =
      applyMotorDirection(rawCommand, STEER_MOTOR_REVERSED);

  if (driveAssistEnabled) beginDriveAssist(direction);

  digitalWrite(STEER_EN_PIN, HIGH);
  writeOneMotor(command,
                STEER_RPWM_PIN,
                STEER_RPWM_CHANNEL,
                STEER_LPWM_PIN,
                STEER_LPWM_CHANNEL);
  delay(moveDurationMs);
  writeOneMotor(0,
                STEER_RPWM_PIN,
                STEER_RPWM_CHANNEL,
                STEER_LPWM_PIN,
                STEER_LPWM_CHANNEL);
  digitalWrite(STEER_EN_PIN, LOW);

  steerStepCount = clampedTarget;

  if (driveAssistEnabled) endDriveAssist();
}

// 조향 바퀴를 손으로 좌측 끝까지 돌려놓은 상태에서 호출하면, 그 위치를
// -STEER_MAX_STEPS로 기준을 맞춘다. 모터는 움직이지 않는다.
void calibrateSteerLeftLimit() {
  steerStepCount = -STEER_MAX_STEPS;
}

bool attachMotorPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(LEFT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(LEFT_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(RIGHT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(RIGHT_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(STEER_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(STEER_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
#else
  ledcSetup(LEFT_RPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(LEFT_LPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(RIGHT_RPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(RIGHT_LPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(STEER_RPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcSetup(STEER_LPWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);

  ledcAttachPin(LEFT_RPWM_PIN, LEFT_RPWM_CHANNEL);
  ledcAttachPin(LEFT_LPWM_PIN, LEFT_LPWM_CHANNEL);
  ledcAttachPin(RIGHT_RPWM_PIN, RIGHT_RPWM_CHANNEL);
  ledcAttachPin(RIGHT_LPWM_PIN, RIGHT_LPWM_CHANNEL);
  ledcAttachPin(STEER_RPWM_PIN, STEER_RPWM_CHANNEL);
  ledcAttachPin(STEER_LPWM_PIN, STEER_LPWM_CHANNEL);
  return true;
#endif
}

void connectToWifi() {
  stopDrive();

  WiFi.setHostname(MDNS_HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.printf("Connecting to %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    stopDrive();
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

void setupWebServer() {
  server.on("/", HTTP_GET, []() {
    server.send_P(
        200,
        "text/html; charset=utf-8",
        CONTROL_PAGE);
  });

  server.on("/f", HTTP_GET, []() {
    runForward();
    server.send(200, "text/plain; charset=utf-8", "FORWARD");
  });

  server.on("/b", HTTP_GET, []() {
    runBackward();
    server.send(200, "text/plain; charset=utf-8", "BACKWARD");
  });

  server.on("/s", HTTP_GET, []() {
    stopDrive();
    server.send(200, "text/plain; charset=utf-8", "STOP");
  });

  server.on("/steer_left", HTTP_GET, []() {
    moveSteeringToStep(-STEER_MAX_STEPS);
    server.send(200, "text/plain; charset=utf-8", String(steerStepCount));
  });

  server.on("/steer_right", HTTP_GET, []() {
    moveSteeringToStep(STEER_MAX_STEPS);
    server.send(200, "text/plain; charset=utf-8", String(steerStepCount));
  });

  server.on("/steer_init", HTTP_GET, []() {
    calibrateSteerLeftLimit();
    server.send(200, "text/plain; charset=utf-8", String(steerStepCount));
  });

  server.on("/assist_toggle", HTTP_GET, []() {
    driveAssistEnabled = !driveAssistEnabled;
    server.send(
        200,
        "text/plain; charset=utf-8",
        driveAssistEnabled ? "ON" : "OFF");
  });

  server.on("/assist_speed", HTTP_GET, []() {
    if (!server.hasArg("value")) {
      server.send(400, "text/plain; charset=utf-8", "MISSING VALUE");
      return;
    }

    const int requestedPercent = server.arg("value").toInt();
    if (requestedPercent < ASSIST_SPEED_PERCENT_MIN ||
        requestedPercent > ASSIST_SPEED_PERCENT_MAX) {
      server.send(400, "text/plain; charset=utf-8", "RANGE: 20-100");
      return;
    }

    assistSpeedPercent = static_cast<uint8_t>(requestedPercent);
    assistDuty = static_cast<uint8_t>(
        (static_cast<uint16_t>(assistSpeedPercent) * PWM_MAX + 50) / 100);

    Serial.printf(
        "Assist speed: %u%%, duty: %u/255\n",
        assistSpeedPercent,
        assistDuty);
    server.send(200, "text/plain; charset=utf-8", "ASSIST SPEED UPDATED");
  });

  server.on("/speed", HTTP_GET, []() {
    if (!server.hasArg("value")) {
      server.send(400, "text/plain; charset=utf-8", "MISSING VALUE");
      return;
    }

    const int requestedPercent = server.arg("value").toInt();
    if (requestedPercent < 20 || requestedPercent > 100) {
      server.send(400, "text/plain; charset=utf-8", "RANGE: 20-100");
      return;
    }

    driveSpeedPercent = static_cast<uint8_t>(requestedPercent);
    driveDuty = static_cast<uint8_t>(
        (static_cast<uint16_t>(driveSpeedPercent) * PWM_MAX + 50) / 100);
    applySpeedToRunningMotors();

    Serial.printf(
        "Speed: %u%%, duty: %u/255\n",
        driveSpeedPercent,
        driveDuty);
    server.send(200, "text/plain; charset=utf-8", "SPEED UPDATED");
  });

  server.onNotFound([]() {
    server.send(404, "text/plain; charset=utf-8", "NOT FOUND");
  });

  server.begin();

  if (MDNS.begin(MDNS_HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.printf("Open: http://%s.local\n", MDNS_HOSTNAME);
  } else {
    Serial.println("mDNS failed. Use the printed IP address.");
  }
}

void setup() {
  Serial.begin(921600);

  // PWM 및 Wi-Fi 설정 전부터 Enable을 Low로 유지한다.
  pinMode(MOTOR_EN_PIN, OUTPUT);
  digitalWrite(MOTOR_EN_PIN, LOW);
  pinMode(STEER_EN_PIN, OUTPUT);
  digitalWrite(STEER_EN_PIN, LOW);

  if (!attachMotorPwm()) {
    Serial.println("[ERROR] PWM initialization failed.");
    while (true) {
      digitalWrite(MOTOR_EN_PIN, LOW);
      delay(1000);
    }
  }

  stopDrive();
  connectToWifi();
  setupWebServer();
  wifiWasConnected = true;
}

void loop() {
  const bool wifiConnected = WiFi.status() == WL_CONNECTED;

  if (!wifiConnected) {
    if (wifiWasConnected) {
      stopDrive();
      wifiWasConnected = false;
      Serial.println("Wi-Fi disconnected: motors stopped.");
    }

    delay(10);
    return;
  }

  wifiWasConnected = true;
  server.handleClient();

  if (driveState != DriveState::STOPPED &&
      millis() - lastMotorCommandMs > COMMAND_TIMEOUT_MS) {
    stopDrive();
    Serial.println("Command timeout: motors stopped.");
  }
}

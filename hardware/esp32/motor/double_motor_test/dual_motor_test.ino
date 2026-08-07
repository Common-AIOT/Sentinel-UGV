#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_arduino_version.h>

// 반드시 사용하는 네트워크 정보로 수정한다.
constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char MDNS_HOSTNAME[] = "esp32-dual-motor";

// ESP32 DevKit + BTS7960(IBT-2) 2개
constexpr uint8_t LEFT_RPWM_PIN = 25;
constexpr uint8_t LEFT_LPWM_PIN = 26;
constexpr uint8_t RIGHT_RPWM_PIN = 32;
constexpr uint8_t RIGHT_LPWM_PIN = 33;

// 두 드라이버의 R_EN, L_EN을 함께 제어하는 공통 Enable
constexpr uint8_t MOTOR_EN_PIN = 23;

// Arduino-ESP32 Core 2.x에서만 사용하는 LEDC 채널 번호
constexpr uint8_t LEFT_RPWM_CHANNEL = 0;
constexpr uint8_t LEFT_LPWM_CHANNEL = 1;
constexpr uint8_t RIGHT_RPWM_CHANNEL = 2;
constexpr uint8_t RIGHT_LPWM_CHANNEL = 3;

constexpr uint32_t PWM_FREQUENCY_HZ = 20000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr int16_t PWM_MAX = 255;
constexpr uint16_t DIRECTION_DEAD_TIME_MS = 50;

// 브라우저에서 명령이 끊기거나 Wi-Fi가 끊기면 자동 정지한다.
constexpr uint32_t COMMAND_TIMEOUT_MS = 1500;

// 전진 명령에서 한쪽 바퀴가 반대로 돌면 해당 값만 바꾼다.
constexpr bool LEFT_MOTOR_REVERSED = false;
constexpr bool RIGHT_MOTOR_REVERSED = true;

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

bool attachMotorPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return ledcAttach(LEFT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(LEFT_LPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
         ledcAttach(RIGHT_RPWM_PIN, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS) &&
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

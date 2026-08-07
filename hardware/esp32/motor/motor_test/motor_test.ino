#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_arduino_version.h>

// 반드시 사용하는 네트워크 정보로 수정한다.
constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char MDNS_HOSTNAME[] = "esp32-motor";

// ESP32 DevKit 기준 BTS7960 제어 핀
constexpr uint8_t RPWM_PIN = 25;
constexpr uint8_t LPWM_PIN = 26;
constexpr uint8_t EN_PIN = 23;  // R_EN과 L_EN을 함께 연결

constexpr uint32_t PWM_FREQUENCY = 20000;
constexpr uint8_t PWM_RESOLUTION = 8;
constexpr uint8_t TEST_DUTY = 128;  // 0~255 중 약 25%

// Arduino-ESP32 Core 2.x에서만 사용하는 LEDC 채널
constexpr uint8_t RPWM_CHANNEL = 0;
constexpr uint8_t LPWM_CHANNEL = 1;

// 브라우저에서 명령이 끊기면 자동 정지
constexpr uint32_t COMMAND_TIMEOUT_MS = 1500;

WebServer server(80);

enum class MotorState {
  STOPPED,
  FORWARD,
  BACKWARD
};

MotorState motorState = MotorState::STOPPED;
uint32_t lastMotorCommandMs = 0;
bool wifiWasConnected = false;

const char CONTROL_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ESP32 모터 제어</title>
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
      width: min(92vw, 420px);
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
  </style>
</head>
<body>
  <main>
    <h1>BTS7960 모터 제어</h1>
    <p>버튼 또는 키보드 F/B를 누르는 동안 동작합니다.</p>
    <div id="status">정지</div>
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

void stopMotor() {
  writePwm(RPWM_PIN, RPWM_CHANNEL, 0);
  writePwm(LPWM_PIN, LPWM_CHANNEL, 0);
  digitalWrite(EN_PIN, LOW);
  motorState = MotorState::STOPPED;
}

void runForward() {
  if (motorState != MotorState::FORWARD) {
    stopMotor();
    delay(100);

    digitalWrite(EN_PIN, HIGH);
    writePwm(LPWM_PIN, LPWM_CHANNEL, 0);
    writePwm(RPWM_PIN, RPWM_CHANNEL, TEST_DUTY);
    motorState = MotorState::FORWARD;
  }

  lastMotorCommandMs = millis();
}

void runBackward() {
  if (motorState != MotorState::BACKWARD) {
    stopMotor();
    delay(100);

    digitalWrite(EN_PIN, HIGH);
    writePwm(RPWM_PIN, RPWM_CHANNEL, 0);
    writePwm(LPWM_PIN, LPWM_CHANNEL, TEST_DUTY);
    motorState = MotorState::BACKWARD;
  }

  lastMotorCommandMs = millis();
}

void setupPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const bool rpwmReady =
      ledcAttach(RPWM_PIN, PWM_FREQUENCY, PWM_RESOLUTION);
  const bool lpwmReady =
      ledcAttach(LPWM_PIN, PWM_FREQUENCY, PWM_RESOLUTION);

  if (!rpwmReady || !lpwmReady) {
    stopMotor();
    while (true) {
      delay(1000);
    }
  }
#else
  ledcSetup(RPWM_CHANNEL, PWM_FREQUENCY, PWM_RESOLUTION);
  ledcSetup(LPWM_CHANNEL, PWM_FREQUENCY, PWM_RESOLUTION);
  ledcAttachPin(RPWM_PIN, RPWM_CHANNEL);
  ledcAttachPin(LPWM_PIN, LPWM_CHANNEL);
#endif
}

void connectToWifi() {
  stopMotor();

  // setHostname은 Wi-Fi 시작 전에 호출해야 한다.
  WiFi.setHostname(MDNS_HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.printf("Connecting to %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    stopMotor();
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
    server.send(200, "text/plain", "FORWARD");
  });

  server.on("/b", HTTP_GET, []() {
    runBackward();
    server.send(200, "text/plain", "BACKWARD");
  });

  server.on("/s", HTTP_GET, []() {
    stopMotor();
    server.send(200, "text/plain", "STOP");
  });

  server.onNotFound([]() {
    server.send(404, "text/plain", "NOT FOUND");
  });

  server.begin();

  if (MDNS.begin(MDNS_HOSTNAME)) {
    MDNS.addService("http", "tcp", 80);
    Serial.println("Open: http://esp32-motor.local");
  } else {
    Serial.println("mDNS failed. Use the printed IP address.");
  }
}

void setup() {
  Serial.begin(921600);

  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, LOW);

  setupPwm();
  stopMotor();
  connectToWifi();
  setupWebServer();

  wifiWasConnected = true;
}

void loop() {
  const bool wifiConnected = WiFi.status() == WL_CONNECTED;

  if (!wifiConnected) {
    if (wifiWasConnected) {
      stopMotor();
      wifiWasConnected = false;
    }

    delay(10);
    return;
  }

  wifiWasConnected = true;
  server.handleClient();

  if (motorState != MotorState::STOPPED &&
      millis() - lastMotorCommandMs > COMMAND_TIMEOUT_MS) {
    stopMotor();
  }
}

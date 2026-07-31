#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <esp_arduino_version.h>

// 반드시 사용하는 네트워크 정보로 수정한다.
constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
constexpr char MDNS_HOSTNAME[] = "esp32-tank-drive";

// ESP32 DevKit + BTS7960(IBT-2) 2개 (좌/우 주행 모터)
// 조향 모터는 제거하고, 두 모터의 속도 차(differential)만으로
// 전진/후진/회전을 모두 처리하는 탱크 드라이브 방식으로 바꿨다.
constexpr uint8_t LEFT_RPWM_PIN = 25;
constexpr uint8_t LEFT_LPWM_PIN = 26;
constexpr uint8_t RIGHT_RPWM_PIN = 32;
constexpr uint8_t RIGHT_LPWM_PIN = 33;

// 두 주행 드라이버의 R_EN, L_EN을 함께 제어하는 공통 Enable
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
// 이 플래그는 "각 바퀴가 +일 때 전진"이 되도록 배선을 보정하는 용도다.
constexpr bool LEFT_MOTOR_REVERSED = false;
constexpr bool RIGHT_MOTOR_REVERSED = true;

// 주행 속도(전/후진)와 회전 속도(좌/우 차동량)를 각각 조절한다.
constexpr uint8_t SPEED_PERCENT_MIN = 20;
constexpr uint8_t SPEED_PERCENT_MAX = 100;

static inline uint8_t percentToDuty(uint8_t percent) {
  return static_cast<uint8_t>(
      (static_cast<uint16_t>(percent) * PWM_MAX + 50) / 100);
}

WebServer server(80);

// 주행 속도(전/후진 기본 duty)와 회전 속도(좌/우 차동 duty)
uint8_t driveSpeedPercent = 40;
uint8_t driveDuty = percentToDuty(40);
uint8_t turnSpeedPercent = 55;
uint8_t turnDuty = percentToDuty(55);

// 마지막 주행 의도. f: -1(후진)/0/+1(전진), t: -1(좌)/0/+1(우)
int8_t lastDriveF = 0;
int8_t lastDriveT = 0;
bool driveActive = false;

int16_t currentLeftCommand = 0;
int16_t currentRightCommand = 0;
uint32_t lastMotorCommandMs = 0;
bool wifiWasConnected = false;

const char CONTROL_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ESP32 탱크 드라이브 제어</title>
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
    h1 { font-size: 1.4rem; }
    p { color: #cbd5e1; }
    #status {
      min-height: 1.5em;
      color: #facc15;
      font-weight: 700;
      font-size: 1.2rem;
    }
    .pad {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      margin: 16px 0;
    }
    .pad button {
      min-height: 84px;
      border: 0;
      border-radius: 14px;
      color: white;
      font-size: 1.3rem;
      font-weight: 700;
      touch-action: none;
      user-select: none;
    }
    .spacer { visibility: hidden; }
    #forward { background: #15803d; }
    #backward { background: #1d4ed8; }
    #left { background: #b45309; }
    #right { background: #0f766e; }
    #stop { background: #b91c1c; }
    .speed {
      margin: 16px 0;
      padding: 16px;
      border-radius: 14px;
      background: #1f2937;
      text-align: left;
    }
    label {
      display: block;
      margin-bottom: 10px;
      font-weight: 700;
    }
    input[type="range"] { width: 100%; }
  </style>
</head>
<body>
  <main>
    <h1>탱크 드라이브 Wi-Fi 제어</h1>
    <p>버튼을 누르는 동안, 또는 키보드 W/A/S/D · 방향키로 주행합니다.
      두 방향을 함께 누르면 곡선 주행(예: 전진+좌)이 됩니다.</p>
    <div id="status">정지</div>

    <div class="pad">
      <div class="spacer"></div>
      <button id="forward">▲ 전진 (W)</button>
      <div class="spacer"></div>
      <button id="left">◀ 좌 (A)</button>
      <button id="stop">■ 정지</button>
      <button id="right">우 ▶ (D)</button>
      <div class="spacer"></div>
      <button id="backward">▼ 후진 (S)</button>
      <div class="spacer"></div>
    </div>

    <div class="speed">
      <label for="speed">주행 속도: <span id="speedValue">40</span>%</label>
      <input id="speed" type="range" min="20" max="100" value="40">
      <label for="turn" style="margin-top:16px;">회전 속도:
        <span id="turnValue">55</span>%</label>
      <input id="turn" type="range" min="20" max="100" value="55">
    </div>
  </main>

  <script>
    // 주행 의도 상태. 버튼과 키보드가 같은 상태를 공유한다.
    const held = { fwd: false, back: false, left: false, right: false };
    let pollTimer = null;

    function computeFT() {
      const f = (held.fwd ? 1 : 0) - (held.back ? 1 : 0);
      const t = (held.right ? 1 : 0) - (held.left ? 1 : 0);
      return { f, t };
    }

    function statusText(f, t) {
      if (f > 0 && t < 0) return '전진 + 좌';
      if (f > 0 && t > 0) return '전진 + 우';
      if (f < 0 && t < 0) return '후진 + 좌';
      if (f < 0 && t > 0) return '후진 + 우';
      if (f > 0) return '전진';
      if (f < 0) return '후진';
      if (t < 0) return '좌회전';
      if (t > 0) return '우회전';
      return '정지';
    }

    function sendDrive() {
      const { f, t } = computeFT();
      document.getElementById('status').textContent = statusText(f, t);
      fetch('/drive?f=' + f + '&t=' + t, { cache: 'no-store' })
        .catch(() => {});
    }

    // 어느 방향이든 눌려 있으면 주기적으로 재전송해 타임아웃 정지를 막고,
    // 모두 떼면 즉시 정지 명령을 보낸다.
    function refresh() {
      const active = held.fwd || held.back || held.left || held.right;
      if (active) {
        sendDrive();
        if (pollTimer === null) {
          pollTimer = setInterval(sendDrive, 400);
        }
      } else {
        if (pollTimer !== null) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        document.getElementById('status').textContent = '정지';
        fetch('/drive?f=0&t=0', { cache: 'no-store' }).catch(() => {});
      }
    }

    function setHeld(dir, value) {
      if (held[dir] === value) return;
      held[dir] = value;
      refresh();
    }

    function stopAll() {
      held.fwd = held.back = held.left = held.right = false;
      refresh();
    }

    function bindButton(id, dir) {
      const el = document.getElementById(id);
      el.addEventListener('pointerdown', () => setHeld(dir, true));
      el.addEventListener('pointerup', () => setHeld(dir, false));
      el.addEventListener('pointercancel', () => setHeld(dir, false));
      el.addEventListener('pointerleave', () => setHeld(dir, false));
    }

    bindButton('forward', 'fwd');
    bindButton('backward', 'back');
    bindButton('left', 'left');
    bindButton('right', 'right');
    document.getElementById('stop').addEventListener('click', stopAll);

    const keyMap = {
      'w': 'fwd', 'W': 'fwd', 'ArrowUp': 'fwd',
      's': 'back', 'S': 'back', 'ArrowDown': 'back',
      'a': 'left', 'A': 'left', 'ArrowLeft': 'left',
      'd': 'right', 'D': 'right', 'ArrowRight': 'right'
    };

    document.addEventListener('keydown', event => {
      if (event.key === ' ' || event.key === 'Escape') {
        event.preventDefault();
        stopAll();
        return;
      }
      const dir = keyMap[event.key];
      if (dir) {
        event.preventDefault();
        setHeld(dir, true);
      }
    });

    document.addEventListener('keyup', event => {
      const dir = keyMap[event.key];
      if (dir) setHeld(dir, false);
    });

    // 창을 벗어나거나 탭이 숨겨지면 안전하게 정지한다.
    window.addEventListener('blur', stopAll);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopAll();
    });

    const speed = document.getElementById('speed');
    const speedValue = document.getElementById('speedValue');
    speed.addEventListener('input', () => {
      speedValue.textContent = speed.value;
    });
    speed.addEventListener('change', () => {
      fetch('/speed?value=' + speed.value, { cache: 'no-store' })
        .catch(() => {});
    });

    const turn = document.getElementById('turn');
    const turnValue = document.getElementById('turnValue');
    turn.addEventListener('input', () => {
      turnValue.textContent = turn.value;
    });
    turn.addEventListener('change', () => {
      fetch('/turn_speed?value=' + turn.value, { cache: 'no-store' })
        .catch(() => {});
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
// 좌/우 각각 논리적으로 +가 전진이 되도록 배선 보정 후 출력한다.
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
  lastDriveF = 0;
  lastDriveT = 0;
  driveActive = false;
}

// 탱크 드라이브 믹싱.
//   base = f * driveDuty   (전/후진 성분)
//   turn = t * turnDuty    (좌/우 차동 성분)
//   left  = base + turn,  right = base - turn  (각각 ±255로 clamp)
// f=0 이고 t!=0 이면 제자리 회전, f!=0 이고 t!=0 이면 곡선 주행이 된다.
// t=+1(우회전) 시 왼쪽이 앞서고 오른쪽이 뒤로 밀려 시계방향으로 돈다.
// 실제로 좌/우가 반대로 돌면 아래 turn 부호를 바꾸거나 좌/우 핀 정의를
// 서로 맞바꾼다.
void applyTankDrive(int8_t f, int8_t t) {
  lastDriveF = constrain(f, -1, 1);
  lastDriveT = constrain(t, -1, 1);

  const int16_t base = static_cast<int16_t>(lastDriveF) * driveDuty;
  const int16_t turn = static_cast<int16_t>(lastDriveT) * turnDuty;

  const int16_t left = constrain(base + turn, -PWM_MAX, PWM_MAX);
  const int16_t right = constrain(base - turn, -PWM_MAX, PWM_MAX);

  setDrive(left, right);
  driveActive = (left != 0 || right != 0);
  lastMotorCommandMs = millis();
}

// 속도 슬라이더 변경 시, 주행 중이면 같은 의도로 즉시 다시 적용한다.
void reapplyDrive() {
  if (driveActive) {
    applyTankDrive(lastDriveF, lastDriveT);
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

void handleSpeedArg(uint8_t& percentTarget, uint8_t& dutyTarget,
                    const char* label) {
  if (!server.hasArg("value")) {
    server.send(400, "text/plain; charset=utf-8", "MISSING VALUE");
    return;
  }

  const int requested = server.arg("value").toInt();
  if (requested < SPEED_PERCENT_MIN || requested > SPEED_PERCENT_MAX) {
    server.send(400, "text/plain; charset=utf-8", "RANGE: 20-100");
    return;
  }

  percentTarget = static_cast<uint8_t>(requested);
  dutyTarget = percentToDuty(percentTarget);
  reapplyDrive();

  Serial.printf("%s: %u%%, duty: %u/255\n", label, percentTarget, dutyTarget);
  server.send(200, "text/plain; charset=utf-8", "UPDATED");
}

void setupWebServer() {
  server.on("/", HTTP_GET, []() {
    server.send_P(200, "text/html; charset=utf-8", CONTROL_PAGE);
  });

  // 메인 주행 엔드포인트: f(-1/0/1), t(-1/0/1)로 탱크 믹싱.
  server.on("/drive", HTTP_GET, []() {
    const int8_t f = server.hasArg("f")
                         ? static_cast<int8_t>(server.arg("f").toInt())
                         : 0;
    const int8_t t = server.hasArg("t")
                         ? static_cast<int8_t>(server.arg("t").toInt())
                         : 0;
    if (f == 0 && t == 0) {
      stopDrive();
    } else {
      applyTankDrive(f, t);
    }
    server.send(200, "text/plain; charset=utf-8", "OK");
  });

  // curl 등으로 손쉽게 테스트하기 위한 단축 엔드포인트(조합 주행은 /drive 사용)
  server.on("/f", HTTP_GET, []() {
    applyTankDrive(1, 0);
    server.send(200, "text/plain; charset=utf-8", "FORWARD");
  });
  server.on("/b", HTTP_GET, []() {
    applyTankDrive(-1, 0);
    server.send(200, "text/plain; charset=utf-8", "BACKWARD");
  });
  server.on("/l", HTTP_GET, []() {
    applyTankDrive(0, -1);
    server.send(200, "text/plain; charset=utf-8", "LEFT");
  });
  server.on("/r", HTTP_GET, []() {
    applyTankDrive(0, 1);
    server.send(200, "text/plain; charset=utf-8", "RIGHT");
  });
  server.on("/s", HTTP_GET, []() {
    stopDrive();
    server.send(200, "text/plain; charset=utf-8", "STOP");
  });

  server.on("/speed", HTTP_GET, []() {
    handleSpeedArg(driveSpeedPercent, driveDuty, "Drive speed");
  });
  server.on("/turn_speed", HTTP_GET, []() {
    handleSpeedArg(turnSpeedPercent, turnDuty, "Turn speed");
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
  Serial.begin(115200);

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

  if (driveActive && millis() - lastMotorCommandMs > COMMAND_TIMEOUT_MS) {
    stopDrive();
    Serial.println("Command timeout: motors stopped.");
  }
}

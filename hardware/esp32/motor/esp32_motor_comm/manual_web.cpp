#include "manual_web.h"

#include <Arduino.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_random.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <fault_codes.h>

#include "board_state.h"
#include "control_page.h"
#include "manual_web_config.h"
#include "mode_arbiter.h"
#include "safety_stub.h"

// 주의: 이 보드의 UART 는 921600 바이너리 프로토콜 전용이다. 여기에
// Serial.print() 디버그를 추가하지 말 것 - 젯슨 쪽 프레이밍이 깨진다.
// 진단은 HTTP 응답의 `st`/`ff`/`wifi` 필드로 낸다.

namespace {

WebServer g_server(MANUAL_WEB_PORT);

constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
uint32_t g_lastWifiAttemptMs = 0;
bool g_wasConnected = false;
bool g_mdnsStarted = false;

// 다른 조종자가 최근 이 시간 안에 명령을 보냈으면 새 세션 발급을 거절한다.
// `MANUAL_FRESHNESS_GUARD_MS` 와 같은 값을 쓰는 것은 우연이 아니다 - 둘 다
// "사람이 지금 잡고 있는가" 를 판정한다.
constexpr uint32_t SESSION_TAKEOVER_GUARD_MS = MANUAL_FRESHNESS_GUARD_MS;

int32_t argInt(const char* name, int32_t fallback) {
  if (!g_server.hasArg(name)) return fallback;
  return (int32_t)g_server.arg(name).toInt();
}

uint32_t argHex32(const char* name) {
  if (!g_server.hasArg(name)) return 0;
  return (uint32_t)strtoul(g_server.arg(name).c_str(), nullptr, 16);
}

// 모든 엔드포인트가 같은 본문을 낸다(비-2xx 포함). 폰은 응답 하나로 화면 전체를
// 다시 그릴 수 있어야 한다 - 별도 조회를 또 하면 그것이 lastManualInputMs 를
// 건드릴 위험이 생긴다.
String stateBody(const MotorSharedState& s, const char* result) {
  char sid[9];
  snprintf(sid, sizeof(sid), "%08lx", (unsigned long)s.manualSessionId);

  const bool driving =
      motorDriverAppliedPwmLeft() != 0 || motorDriverAppliedPwmRight() != 0;

  String out;
  out.reserve(192);
  out += "{\"st\":";
  out += (int)s.state;
  out += ",\"lat\":";
  out += s.manualLatched ? 1 : 0;
  out += ",\"dm\":";
  out += s.manualDeadman ? 1 : 0;
  out += ",\"rearm\":";
  out += s.manualReArmRequired ? 1 : 0;
  out += ",\"dz\":";
  out += driveDirectionChangePending() ? 1 : 0;
  // 조향이 걸리지 않는 상태인가. 폰이 「조향 불가」를 띄우는 근거다.
  out += ",\"nosteer\":";
  out += s.manualSteeringRequested ? 0 : 1;
  out += ",\"pwm\":";
  out += driving ? (int)motorDriverAppliedPwmLeft() : 0;
  out += ",\"sdeg\":";
  out += (int)s.manualSteeringMdeg;
  out += ",\"ff\":";
  out += (int)s.faultFlags;
  out += ",\"ttl\":";
  out += (int)s.manualTtlMs;
  out += ",\"wifi\":";
  out += manualWebConnected() ? 1 : 0;
  out += ",\"sid\":\"";
  out += sid;
  out += "\",\"res\":\"";
  out += result;
  out += "\"}";
  return out;
}

// **`server.send()` 는 mutex 밖에서 부른다.** 소켓 쓰기가 블로킹될 수 있고 그동안
// 100Hz 제어 루프가 뮤텍스를 기다리며 멈춘다.
void respond(int code, const MotorSharedState& snapshot, const char* result) {
  g_server.send(code, "application/json; charset=utf-8", stateBody(snapshot, result));
}

void handleRoot() {
  g_server.send_P(200, "text/html; charset=utf-8", CONTROL_PAGE);
}

// 새 조종 세션을 발급한다.
//
// **`manualLatched` 를 건드리지 않는다.** 새로고침이 자율에 권한을 돌려주면 안
// 되고, 반대로 세션 발급만으로 래치가 걸려서도 안 된다 - 래치는 deadman 이 눌린
// 패킷 2개·100ms 가 만든다.
void handleSession() {
  MotorSharedState snapshot{};
  bool conflict = false;

  motorSharedStateUpdate([&](MotorSharedState& s) {
    const uint32_t now = millis();
    const bool otherActive =
        s.manualSessionId != 0 && s.hasManualInput &&
        (now - s.lastManualInputMs) < SESSION_TAKEOVER_GUARD_MS;
    if (otherActive) {
      conflict = true;
      snapshot = s;
      return;
    }
    uint32_t sid = esp_random();
    if (sid == 0) sid = 1;  // 0 은 「세션 없음」 sentinel 이다
    s.manualSessionId = sid;
    // 새 세션은 시퀀스를 처음부터 센다. 폰이 새로고침으로 seq 를 0 부터 다시
    // 보내도 REJECTED_SEQUENCE 로 막히지 않게 한다.
    s.hasManualSequence = false;
    s.manualDeadman = false;
    s.manualRunActive = false;
    s.manualRunPackets = 0;
    snapshot = s;
  });

  respond(conflict ? 409 : 200, snapshot, conflict ? "SESSION_BUSY" : "ACCEPTED");
}

void handleDrive() {
  ManualPacket packet{};
  packet.sessionId = argHex32("sid");
  packet.sequence = (uint16_t)(argInt("seq", 0) & 0xFFFF);
  packet.deadman = argInt("dm", 0) != 0;
  packet.linMilli = (int16_t)argInt("lin", 0);
  packet.angMilli = (int16_t)argInt("ang", 0);
  packet.ttlMs = (uint16_t)argInt("ttl", MANUAL_TTL_DEFAULT_MS);

  ManualResult result = ManualResult::REJECTED_ARGUMENT;
  MotorSharedState snapshot{};
  motorSharedStateUpdate([&](MotorSharedState& s) {
    result = ingestManualPacket(s, packet, millis());
    snapshot = s;
  });

  switch (result) {
    case ManualResult::ACCEPTED:
      respond(200, snapshot, "ACCEPTED");
      break;
    case ManualResult::REJECTED_LATCHED:
      respond(423, snapshot, "BOARD_LATCHED");
      break;
    case ManualResult::REJECTED_SESSION:
      respond(403, snapshot, "SESSION_MISMATCH");
      break;
    case ManualResult::REJECTED_SEQUENCE:
      respond(409, snapshot, "STALE_SEQUENCE");
      break;
    case ManualResult::REJECTED_ARGUMENT:
    default:
      respond(400, snapshot, "BAD_ARGUMENT");
      break;
  }
}

// **세션이 달라도 수락한다.** 누구의 정지든 항상 존중한다 - 정지를 거절하는
// 이유는 어떤 것도 안전보다 무겁지 않다.
void handleStop() {
  MotorSharedState snapshot{};
  motorSharedStateUpdate([&](MotorSharedState& s) {
    s.manualDeadman = false;
    s.manualDriveMmps = 0;
    s.manualSteeringRequested = false;
    s.manualRunActive = false;
    s.manualRunPackets = 0;
    // TTL 만료를 기다리지 않도록 입력 시각은 갱신한다. 래치는 건드리지 않는다 -
    // 「정지」는 모드 이탈이 아니다.
    if (s.manualSessionId != 0) {
      s.lastManualInputMs = millis();
      s.hasManualInput = true;
    }
    snapshot = s;
  });
  respond(200, snapshot, "ACCEPTED");
}

// **부작용이 하나도 없어야 한다.**
//
// 이 핸들러가 `lastManualInputMs` 를 갱신하면 관제의 500ms 신선도 창이 영구히
// 닫히지 않아 `SET_MODE(AUTO)` 가 항상 거부되고 **로봇을 되찾을 수 없다.**
// 폰은 유휴 상태에서 이 경로를 2Hz 로 폴링하므로 실수하면 즉시 그렇게 된다.
// HTTP 계층에서 가장 중요한 금지사항이다.
void handleState() {
  const MotorSharedState snapshot = motorSharedStateSnapshot();
  respond(200, snapshot, "ACCEPTED");
}

void handleNotFound() {
  const MotorSharedState snapshot = motorSharedStateSnapshot();
  respond(404, snapshot, "NOT_FOUND");
}

void startWifi() {
  WiFi.setHostname(MANUAL_MDNS_HOSTNAME);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  // 모뎀 슬립의 ~100ms 지연 스파이크가 250ms TTL 을 깬다. 전류를 조금 더 쓰더라도
  // 끈다(tank_drive 에서 확인한 값).
  WiFi.setSleep(false);
  WiFi.begin(MANUAL_WIFI_SSID, MANUAL_WIFI_PASSWORD);
  g_lastWifiAttemptMs = millis();
}

// WiFi 가 끊긴 전이에서 바퀴를 세운다. **`manualLatched` 는 건드리지 않는다** -
// 링크 상실은 모드 이탈이 아니고, 젯슨에 자율 권한을 돌려줄 근거도 아니다.
void onWifiLost() {
  motorSharedStateUpdate([](MotorSharedState& s) {
    s.manualDeadman = false;
    s.manualDriveMmps = 0;
    s.manualSteeringRequested = false;
    s.manualRunActive = false;
    s.manualRunPackets = 0;
  });
}

}  // namespace

bool manualWebConnected() {
  return WiFi.status() == WL_CONNECTED;
}

void manualWebInit() {
  startWifi();

  g_server.on("/", HTTP_GET, handleRoot);
  g_server.on("/manual/session", HTTP_GET, handleSession);
  g_server.on("/manual/drive", HTTP_GET, handleDrive);
  g_server.on("/manual/stop", HTTP_GET, handleStop);
  g_server.on("/manual/state", HTTP_GET, handleState);
  g_server.onNotFound(handleNotFound);
  g_server.begin();
}

void manualWebTaskFn(void* pvParameters) {
  (void)pvParameters;

  for (;;) {
    const bool connected = manualWebConnected();

    if (connected && !g_wasConnected) {
      if (!g_mdnsStarted && MDNS.begin(MANUAL_MDNS_HOSTNAME)) {
        MDNS.addService("http", "tcp", MANUAL_WEB_PORT);
        g_mdnsStarted = true;
      }
    } else if (!connected && g_wasConnected) {
      onWifiLost();
    } else if (!connected) {
      const uint32_t now = millis();
      if (now - g_lastWifiAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
        // 재시도도 비블로킹이다. 폰 핫스팟이 나중에 켜져도 붙는다.
        WiFi.disconnect();
        startWifi();
      }
    }
    g_wasConnected = connected;

    g_server.handleClient();
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

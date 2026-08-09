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
#include "boot_status.h"
#include "control_page.h"
#include "manual_web_config.h"
#include "mode_arbiter.h"
#include "safety_stub.h"
#include "steering.h"
#include "steering_limits.h"

// 주의: 이 보드의 UART 는 921600 바이너리 프로토콜 전용이다. 여기에
// Serial.print() 디버그를 추가하지 말 것 - 젯슨 쪽 프레이밍이 깨진다.
// 진단은 HTTP 응답의 `st`/`ff`/`wifi` 필드로 낸다.

namespace {

WebServer g_server(MANUAL_WEB_PORT);

constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
// mDNS 등록 재시도 간격. 첫 시도가 실패해도 이름 접속이 죽은 채 남지 않게 한다.
constexpr uint32_t MDNS_RETRY_INTERVAL_MS = 3000;
uint32_t g_lastWifiAttemptMs = 0;
uint32_t g_lastMdnsAttemptMs = 0;
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

// 지금 캘리브레이션 쓰기를 받아도 되는가 (S15P11A301-312).
//
// 이 조건이 이 파일에서 가장 중요한 안전 판정이다. 캘리브레이션은 중립·게인·서보
// 출력을 통째로 바꾸므로, 잘못된 순간에 통과시키면 다음 세 가지가 일어난다.
//
//  1. **자율 주행 중**: 오프셋을 줄이면 젯슨이 보낸 δ 가 조용히 덜 꺾인다. 젯슨
//     vehicle_kinematics 는 여전히 30° 로 계산하므로 궤적이 어긋나고, 개루프라
//     아무도 그것을 감지하지 못한다. 그래서 AUTO_ACTIVE 는 무조건 거부다.
//  2. **바퀴가 도는 중**: 중립을 옮기면 주행 중 앞바퀴가 즉시 그만큼 꺾인다.
//  3. **ESTOP/FAULT 래치 중**: 래치 상태는 불변이어야 한다(SR-008).
//
// 서보 출력 끄기(disarm)도 같은 게이트를 쓴다 - 주행 중에 서보를 free 로 만들면
// 앞바퀴가 노면 힘에 끌려 제멋대로 돈다.
bool calibrationAllowed(const MotorSharedState& s) {
  if (s.state == MotorBoardState::AUTO_ACTIVE) return false;
  if (s.state == MotorBoardState::ESTOP_LATCHED) return false;
  if (s.state == MotorBoardState::FAULT_LATCHED) return false;
  if (s.manualDeadman) return false;
  if (s.manualDriveMmps != 0) return false;
  // 장부만 0 이고 실제 PWM 이 아직 살아 있는 창(방향 전환 데드타임 등)도 막는다.
  if (motorDriverAppliedPwmLeft() != 0 || motorDriverAppliedPwmRight() != 0) return false;
  return true;
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
  // 권한이 **어디서 왔는가** (S15P11A301-345). `lat` 이 1 인데 `fb` 가 0 이면 관제가
  // 승인한 수동이고, `fb` 가 1 이면 젯슨 링크 침묵으로 폰이 관제 자리를 대신하고
  // 있다는 뜻이다. 화면이 그 둘을 구분해 보여 준다.
  out += ",\"fb\":";
  out += s.manualFallbackLatched ? 1 : 0;
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
  out += ",\"armed\":";
  out += s.servoArmed ? 1 : 0;
  out += ",\"cdeg\":";
  out += (int)s.servoCenterDeg;
  out += ",\"odeg\":";
  out += (int)s.servoMaxOffsetDeg;
  // 스냅샷에서 계산한다. steering.* 를 읽으면 control_task 와 경쟁이고, 어차피
  // 중앙 60~210·오프셋 0~60 이면 서보 물리 범위(0~270°) 안이라 값이 같다.
  out += ",\"smin\":";
  out += (int)s.servoCenterDeg - (int)s.servoMaxOffsetDeg;
  out += ",\"smax\":";
  out += (int)s.servoCenterDeg + (int)s.servoMaxOffsetDeg;
  out += ",\"ohard\":";
  out += (int)STEERING_OFFSET_HARD_MAX_DEG;
  out += ",\"spd\":";
  out += (int)s.manualSpeedLimitPercent;
  // 지금 캘리브레이션을 받아 줄 수 있는가. 폰이 입력칸을 잠그는 근거다.
  out += ",\"cal\":";
  out += calibrationAllowed(s) ? 1 : 0;
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

// ---- 캘리브레이션 엔드포인트 (S15P11A301-312, drive_test 기능 이식) ----
//
// 전부 같은 모양이다: 인자 검사 → `calibrationAllowed` 게이트 → 뮤텍스 안에서
// **의도만 기록**. 서보를 실제로 움직이는 것은 control_task 의 10ms 틱이다.

// 게이트를 통과하면 mutator 를 돌리고, 아니면 409 로 사유를 돌려준다.
void withCalibrationGate(const std::function<void(MotorSharedState&)>& mutator) {
  MotorSharedState snapshot{};
  bool allowed = false;
  motorSharedStateUpdate([&](MotorSharedState& s) {
    allowed = calibrationAllowed(s);
    if (allowed) mutator(s);
    snapshot = s;
  });
  respond(allowed ? 200 : 409, snapshot, allowed ? "ACCEPTED" : "CAL_BLOCKED");
}

void handleServoArm() {
  withCalibrationGate([](MotorSharedState& s) { s.servoArmed = true; });
}

void handleServoDisarm() {
  withCalibrationGate([](MotorSharedState& s) { s.servoArmed = false; });
}

// 서보 중립. drive_test 의 STEERING_CENTER_ANGLE_DEG 를 런타임으로 연 것이다.
void handleServoCenter() {
  const int32_t deg = argInt("deg", -1);
  if (deg < STEERING_CENTER_HARD_MIN_DEG || deg > STEERING_CENTER_HARD_MAX_DEG) {
    respond(400, motorSharedStateSnapshot(), "BAD_CENTER");
    return;
  }
  withCalibrationGate(
      [deg](MotorSharedState& s) { s.servoCenterDeg = (uint8_t)deg; });
}

// 좌우 최대 조향각. δ_max 가 몇 도의 서보 회전으로 나가는지를 정한다.
void handleServoLimit() {
  const int32_t deg = argInt("deg", -1);
  if (deg < 0 || deg > STEERING_OFFSET_HARD_MAX_DEG) {
    respond(400, motorSharedStateSnapshot(), "BAD_OFFSET");
    return;
  }
  withCalibrationGate(
      [deg](MotorSharedState& s) { s.servoMaxOffsetDeg = (uint8_t)deg; });
}

// 서보 각도 직접 지정(조그). 입력은 **서보 각도(도)** 이고 내부적으로 δ 로 되돌린다.
// 매핑을 하나로 유지해야 슬루레이트와 ±δ_max 클램프가 그대로 걸린다.
void handleServoAngle() {
  const int32_t servoDeg = argInt("deg", -1000);
  const MotorSharedState current = motorSharedStateSnapshot();
  const int32_t centerDeg = (int32_t)current.servoCenterDeg;
  const int32_t offsetDeg = (int32_t)current.servoMaxOffsetDeg;
  if (offsetDeg <= 0 || servoDeg < centerDeg - offsetDeg ||
      servoDeg > centerDeg + offsetDeg) {
    respond(400, current, "BAD_ANGLE");
    return;
  }
  // 서보 각도 → δ(밀리도). offsetDeg 가 δ_max 에 대응하므로 비례식 하나면 된다.
  // δ 로 되돌려 보내는 이유는 매핑을 하나로 유지하기 위해서다 - 그래야 슬루레이트와
  // ±δ_max 클램프가 조그에도 그대로 걸린다.
  const int32_t mdeg =
      ((servoDeg - centerDeg) * (int32_t)STEERING_MAX_MDEG) / offsetDeg;
  withCalibrationGate([mdeg](MotorSharedState& s) {
    s.servoJogMdeg = (int16_t)mdeg;
    s.servoJogPending = true;
  });
}

// 조향 중립 복귀. **정지 경로가 아니라 사람이 누른 명시적 명령이다** - §34-7 이
// 금지하는 것은 정지·워치독·ESTOP 이 조향을 제멋대로 중립으로 되돌리는 것이고,
// 게이트가 바퀴 정지를 이미 확인했으므로 관성 궤적을 바꿀 여지가 없다.
void handleServoRecenter() {
  withCalibrationGate([](MotorSharedState& s) {
    s.servoJogMdeg = 0;
    s.servoJogPending = true;
  });
}

// 수동 주행 최대 속도(%). 캘리브레이션 게이트를 쓰지 않는다 - 주행 중에 속도를
// 낮추는 것은 언제나 안전한 방향이고, 오히려 그때 필요하다.
void handleSpeedLimit() {
  const int32_t percent = argInt("percent", -1);
  if (percent < 0 || percent > 100) {
    respond(400, motorSharedStateSnapshot(), "BAD_SPEED");
    return;
  }
  MotorSharedState snapshot{};
  motorSharedStateUpdate([percent, &snapshot](MotorSharedState& s) {
    s.manualSpeedLimitPercent = (uint8_t)percent;
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

#if ENABLE_MANUAL_WIFI
void startWifi() {
  // setHostname() 은 STA netif 가 이미 있어야 값이 먹는다 - mode() 보다 먼저
  // 부르면 조용히 무시된다(drive_test.ino 와 순서를 맞췄다).
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(MANUAL_MDNS_HOSTNAME);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  // 모뎀 슬립의 ~100ms 지연 스파이크가 250ms TTL 을 깬다. 전류를 조금 더 쓰더라도
  // 끈다(tank_drive 에서 확인한 값).
  WiFi.setSleep(false);
  WiFi.begin(MANUAL_WIFI_SSID, MANUAL_WIFI_PASSWORD);
  g_lastWifiAttemptMs = millis();
}
#endif

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
#if ENABLE_MANUAL_WIFI
  return WiFi.status() == WL_CONNECTED;
#else
  return false;
#endif
}

void manualWebInit() {
#if ENABLE_MANUAL_WIFI
  startWifi();

  g_server.on("/", HTTP_GET, handleRoot);
  g_server.on("/manual/session", HTTP_GET, handleSession);
  g_server.on("/manual/drive", HTTP_GET, handleDrive);
  g_server.on("/manual/stop", HTTP_GET, handleStop);
  g_server.on("/manual/state", HTTP_GET, handleState);
  g_server.on("/manual/speed", HTTP_GET, handleSpeedLimit);
  g_server.on("/manual/servo/arm", HTTP_GET, handleServoArm);
  g_server.on("/manual/servo/disarm", HTTP_GET, handleServoDisarm);
  g_server.on("/manual/servo/center", HTTP_GET, handleServoCenter);
  g_server.on("/manual/servo/limit", HTTP_GET, handleServoLimit);
  g_server.on("/manual/servo/angle", HTTP_GET, handleServoAngle);
  g_server.on("/manual/servo/recenter", HTTP_GET, handleServoRecenter);
  g_server.onNotFound(handleNotFound);
  g_server.begin();
#endif
}

void manualWebTaskFn(void* pvParameters) {
  (void)pvParameters;

#if ENABLE_MANUAL_WIFI
  for (;;) {
    const bool connected = manualWebConnected();
    const uint32_t now = millis();

    if (connected) {
      // **전이에서만 등록하면 안 된다.** 종전 코드는 연결 전이에서 한 번만
      // MDNS.begin() 을 불렀고, 그 한 번이 실패하면(IP 가 완전히 올라오기 전에
      // WL_CONNECTED 가 뜨는 창이 있다) 재시도가 영원히 없었다. 그러면 HTTP 는 IP
      // 로 멀쩡히 되는데 `sentinel-manual.local` 만 죽은 채로 남는다.
      if (!g_mdnsStarted && (now - g_lastMdnsAttemptMs) >= MDNS_RETRY_INTERVAL_MS) {
        g_lastMdnsAttemptMs = now;
        if (MDNS.begin(MANUAL_MDNS_HOSTNAME)) {
          MDNS.addService("http", "tcp", MANUAL_WEB_PORT);
          g_mdnsStarted = true;
        }
      }
    } else if (g_wasConnected) {
      onWifiLost();
      // **끊길 때 반드시 내린다.** 이것이 없으면 g_mdnsStarted 가 true 로 남아
      // 재연결 후 위 재등록을 건너뛰고, 그때부터 이름 접속이 영구히 죽는다.
      if (g_mdnsStarted) {
        MDNS.end();
        g_mdnsStarted = false;
      }
    } else {
      if (now - g_lastWifiAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
        g_lastWifiAttemptMs = now;
        // **STA 를 통째로 재초기화하지 않는다.** disconnect()+startWifi() 는 폰
        // 핫스팟의 WPA2 핸드셰이크+DHCP 가 5초를 넘기면(흔하다) 매 재시도마다
        // 연결을 끝내지도 못하고 처음부터 다시 시작해 영원히 못 붙는 상태가
        // 된다. reconnect() 는 기존 STA 설정을 그대로 두고 연결만 다시 건다
        // (drive_test.ino 와 동일).
        WiFi.reconnect();
      }
    }
    g_wasConnected = connected;

    // 부팅 5초 창의 상태 표시(S15P11A301-312). 창이 닫히면 bootStatusActive() 가
    // false 라 아래 IP 문자열 조립조차 하지 않는다. **Serial(Jetson 전용)이
    // 아니라 Serial2 에만 쓰므로 comm_task 의 타이밍에 영향이 없다.**
    if (bootStatusActive()) {
      char ipBuf[16] = "";
      if (connected) {
        WiFi.localIP().toString().toCharArray(ipBuf, sizeof(ipBuf));
      }
      bootStatusUpdate(connected, ipBuf, g_mdnsStarted ? MANUAL_MDNS_HOSTNAME : "");
    }

    g_server.handleClient();
    vTaskDelay(pdMS_TO_TICKS(2));
  }
#else
  // WiFi/HTTP 채널을 끈 경우에도 태스크 핸들은 유지하되 아무 작업도 하지 않는다.
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
#endif
}

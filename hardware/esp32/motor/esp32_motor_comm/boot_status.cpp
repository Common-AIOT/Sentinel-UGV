#include "boot_status.h"

#include <Arduino.h>

// 기본은 켬. README `## 디버그 주의` 가 요구하는 컴파일 타임 게이트라 빌드
// 정의로만 끈다 - 런타임 분기를 하나 더 만들지 않는다.
#ifndef ENABLE_BOOT_STATUS_SERIAL2
#define ENABLE_BOOT_STATUS_SERIAL2 1
#endif

namespace {

constexpr uint32_t BOOT_STATUS_WINDOW_MS = 5000;
// 창 안에서도 매 2ms 루프마다 쓰면 소용없는 반복이라 사람이 읽을 간격으로
// 줄인다. 연결 여부가 바뀌는 순간은 이 간격을 무시하고 즉시 보여준다.
constexpr uint32_t BOOT_STATUS_REPEAT_MS = 500;

#if ENABLE_BOOT_STATUS_SERIAL2
constexpr uint32_t BOOT_STATUS_BAUD = 921600;
// 모터(25/26/32/33)·EN(23)·서보(18) 어느 핀과도 겹치지 않는다. 스트래핑 핀도
// 아니라 부팅 시퀀스에 영향이 없다.
constexpr uint8_t BOOT_STATUS_RX2_PIN = 16;
constexpr uint8_t BOOT_STATUS_TX2_PIN = 17;
#endif

uint32_t g_windowStartMs = 0;
uint32_t g_lastPrintMs = 0;
bool g_windowClosed = false;
bool g_printedOnce = false;
bool g_lastConnected = false;

}  // namespace

void bootStatusInit() {
#if ENABLE_BOOT_STATUS_SERIAL2
  Serial2.begin(BOOT_STATUS_BAUD, SERIAL_8N1, BOOT_STATUS_RX2_PIN, BOOT_STATUS_TX2_PIN);
  Serial2.println();
  Serial2.println("[BOOT] Sentinel 모터 ESP32 - 5초간 폰 핫스팟 상태를 표시합니다");
#endif
  g_windowStartMs = millis();
}

bool bootStatusActive() {
#if ENABLE_BOOT_STATUS_SERIAL2
  return !g_windowClosed;
#else
  return false;
#endif
}

void bootStatusUpdate(bool connected, const char* ip, const char* mdnsHost) {
#if ENABLE_BOOT_STATUS_SERIAL2
  if (g_windowClosed) return;

  const uint32_t nowMs = millis();
  if (nowMs - g_windowStartMs >= BOOT_STATUS_WINDOW_MS) {
    g_windowClosed = true;
    Serial2.println("[BOOT] 5초 경과 - 상태 표시를 마칩니다 (Jetson 통신은 계속 돌고 있었습니다)");
    return;
  }

  const bool stateChanged = !g_printedOnce || connected != g_lastConnected;
  if (!stateChanged && (nowMs - g_lastPrintMs) < BOOT_STATUS_REPEAT_MS) return;

  g_printedOnce = true;
  g_lastConnected = connected;
  g_lastPrintMs = nowMs;

  if (!connected) {
    Serial2.println("[BOOT] 폰 핫스팟: 연결 대기 중...");
    return;
  }

  Serial2.println("[BOOT] 폰 핫스팟: 연결됨");
  Serial2.print("[BOOT] 접속 주소: http://");
  Serial2.println(ip);
  if (mdnsHost != nullptr && mdnsHost[0] != '\0') {
    Serial2.print("[BOOT]           http://");
    Serial2.print(mdnsHost);
    Serial2.println(".local/ (안드로이드는 미지원일 수 있음, IP 접속 권장)");
  }
#else
  (void)connected;
  (void)ip;
  (void)mdnsHost;
#endif
}

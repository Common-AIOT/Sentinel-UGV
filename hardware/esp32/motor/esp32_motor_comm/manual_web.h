// 수동 조종 WiFi 채널 (S15P11A301-298, docs/05 31-13).
//
// 폰이 자기 핫스팟 위에서 이 보드에 **직결**한다. 젯슨과 관제 PC 는 별개 WiFi 망에
// 있고 젯슨은 폰에 도달할 수 없으므로, 이것이 수동 조종의 유일한 경로다.
//
// ## 이 태스크는 액추에이션 계층을 만지지 않는다
//
// HTTP 핸들러가 하는 일은 `ingestManualPacket()` 한 번의 짧은 mutation 뿐이고,
// 바퀴를 돌리는 것은 core 1 의 `control_task` 다. 그래야 core 0(WiFi/lwIP)과
// core 1(제어) 사이에 진짜 크로스코어 경쟁이 생기지 않는다.
//
// `server.send()` 를 mutex 안에서 부르지 않는다 - 소켓 쓰기가 블로킹될 수 있고
// 그동안 100Hz 제어 루프가 뮤텍스를 기다리며 멈춘다.
//
// ## 인증 경계
//
// 핫스팟 WPA2 + 로컬 세션 id 뿐이다. `sid` 는 **관제의 controlSessionId 가
// 아니다** - 폰은 자기 핫스팟에서 Spring 에 도달할 수 없다. 단일 조종자만
// 강제하며 신원은 아니다(FR-012 영구 미충족, docs/06).
#pragma once

#include <cstdint>

// 현재 기본 빌드는 manual_web_config.h의 ENABLE_MANUAL_WIFI=1로 수동 WiFi/HTTP
// 채널을 활성화한다. 자율 전용 빌드가 필요하면 컴파일 시 0으로 명시한다.

// WiFi STA 시작(**비블로킹**)과 WebServer/mDNS 등록. setup() 에서 태스크 생성 전에
// 한 번 호출한다.
//
// 비블로킹이어야 하는 이유: `while (WiFi.status() != WL_CONNECTED) delay(500)` 을
// setup() 에 두면 폰 핫스팟이 뜨기 전까지 부팅이 멈춰 `Serial.begin(921600)` 이
// 실행되지 않고 젯슨 핸드셰이크가 통째로 실패한다. 수동 채널이 자율 주행의
// 기동 조건이 되어서는 안 된다.
void manualWebInit();

// core 0 에 고정해 돌린다. `WebServer::handleClient()` 를 2ms 주기로 편다.
void manualWebTaskFn(void* pvParameters);

// WiFi 가 붙어 있는가. `/manual/state` 응답과 진단에 쓴다.
bool manualWebConnected();

// 모터 ESP32 펌웨어. **명령 소스가 둘이고 중재자가 이 보드다.**
//
//   젯슨  ── USB 직렬 921600, COBS+CRC16 ──┐
//                                          ├→ control_task (100Hz, 유일한 액추에이터)
//   폰    ── 핫스팟 WiFi, HTTP 20Hz ───────┘      → BTS7960 후륜 2개 + DS51150 조향 서보
//
// 범위: 프레이밍, HELLO/HELLO_ACK, DRIVE_COMMAND/STOP/ESTOP/SET_MODE, 300ms 통신
// 워치독, DRIVE_STATE/DIAGNOSTIC 송신, 수동 조종 HTTP 채널, 액추에이션 중재.
//
// 2026-08-06 하드웨어 변경: 앞쪽 캐스터 2개를 제거하고 전륜 조향부를 복구했다.
// 후륜 DC 모터 2개는 전·후진 전용, 조향은 전륜 서보 1개 담당이다(§6.3, §34-2).
//
// S15P11A301-298 에서 `tank_drive` 스케치를 여기에 병합하고 삭제했다. 두 스케치가
// 같은 핀·같은 LEDC 채널을 쓰는 배타 스케치였고, 잘못 구우면 **강직된 전륜
// 링키지를 거슬러 차동 선회를 시도한다.** 벤치 스케치
// (motor_test/double_motor_test/triple_motor_test/steering_servo_test)는 그대로 둔다.
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "board_state.h"
#include "comm_task.h"
#include "control_task.h"
#include "manual_web.h"
#include "safety_stub.h"
#include "steering.h"

// `xTaskCreate` 의 `usStackDepth` 는 ESP-IDF 에서 **바이트**다. 종전 이름
// `*_STACK_WORDS` 는 vanilla FreeRTOS 규약을 그대로 옮긴 오명이었고, 그대로 두면
// 다음 사람이 4배 계산해 스택을 잘못 잡는다 (S15P11A301-298).
constexpr uint32_t COMM_TASK_STACK_BYTES = 4096;
// 액추에이션 중재가 이 태스크로 모이면서 프레임이 늘었다(arbitrateDrive 결정 구조체
// + steeringSetTarget 의 float 경로).
constexpr uint32_t CONTROL_TASK_STACK_BYTES = 3072;
// WebServer 는 소켓 버퍼와 String 조립을 스택에서 쓴다. 4096 으로는 헤더가 긴
// 요청에서 넘칠 수 있다.
constexpr uint32_t MANUAL_WEB_TASK_STACK_BYTES = 8192;
constexpr UBaseType_t COMM_TASK_PRIORITY = 2;
// 유일한 액추에이터이므로 시리얼 폴보다 먼저 돌아야 한다. 통신 워치독 판정도 여기서
// 한다.
constexpr UBaseType_t CONTROL_TASK_PRIORITY = 3;
// **lwIP(18)·WiFi(23) 아래여야 한다.** 위에 두면 네트워크 스택이 굶어 20Hz HTTP 가
// 오히려 느려진다.
constexpr UBaseType_t MANUAL_WEB_TASK_PRIORITY = 1;

void setup() {
  // §34-6: 전원 인가 직후 PWM=0·driver enable=LOW·조향 중립(δ=0)을 태스크 생성보다
  // 먼저 보장한다. 조향만 중립으로 시작하는 것은 재부팅 직후에 믿을 수 있는 마지막
  // 각도가 없기 때문이며(RAM이 지워졌고 실제 바퀴 각도를 읽을 수단이 없다), 이후
  // 모든 정지 경로에서는 각도를 유지한다(§34-7).
  motorDriverInit();
  steeringInit();
  motorSharedStateInit();

  // **비블로킹이다.** 접속을 여기서 기다리면 폰 핫스팟이 뜨기 전까지 부팅이 멈춰
  // comm_task 의 Serial.begin(921600) 이 실행되지 않고 젯슨 핸드셰이크가 통째로
  // 실패한다. 수동 채널이 자율 주행의 기동 조건이 되어서는 안 된다.
  manualWebInit();

  // core 1: 제어와 시리얼. core 0: WiFi/lwIP 와 웹.
  //
  // WiFi 태스크가 core 0 에 고정돼 있으므로 웹 태스크도 그쪽에 둬서, 100Hz 제어
  // 루프와 921600 RX 폴에서 소켓 작업을 물리적으로 분리한다.
  xTaskCreatePinnedToCore(commTaskFn, "comm_task", COMM_TASK_STACK_BYTES, nullptr,
                           COMM_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(controlTaskFn, "control_task", CONTROL_TASK_STACK_BYTES, nullptr,
                           CONTROL_TASK_PRIORITY, nullptr, 1);
  xTaskCreatePinnedToCore(manualWebTaskFn, "manual_web", MANUAL_WEB_TASK_STACK_BYTES,
                           nullptr, MANUAL_WEB_TASK_PRIORITY, nullptr, 0);
}

void loop() {
  // 모든 동작은 FreeRTOS 태스크(comm_task, control_task)가 담당한다.
  vTaskDelay(pdMS_TO_TICKS(1000));
}

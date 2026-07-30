#pragma once

// 100Hz로 300ms 통신 워치독을 검사하는 FreeRTOS 태스크(§34-7). 실제 BTS7960 PWM 적용은
// 이 티켓 범위 밖이며, 워치독 트립 시 safety_stub.h의 훅만 호출한다.
void controlTaskFn(void* pvParameters);

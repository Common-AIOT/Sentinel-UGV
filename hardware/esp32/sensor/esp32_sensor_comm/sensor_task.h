#pragma once

// 실제 MT6701/DHT-11/HC-SR04 판독을 대신하는 placeholder 태스크. 후속 센서 판독 티켓이
// 이 파일의 본문을 실측 로직으로 교체하는 훅이다. 통신 계층 검증을 위해 그럴듯한 값을
// 주기적으로 채워 넣기만 한다.
void sensorTaskFn(void* pvParameters);

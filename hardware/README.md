# Hardware

차체, 전원, 센서 설계 산출물을 관리합니다. 짐벌은 프로젝트 범위에서 제외했습니다(02장 6.4).

- `cad/`: CAD 원본과 내보내기 지침
- `wiring/`: 전원·신호 배선도와 커넥터 핀맵
- `bom/`: 부품명, 모델, 수량, 전기 사양과 결정 상태
- `esp32/`: 모터·센서 ESP32 펌웨어. `esp32/jetson-comm/`은 두 스케치가 공유하는 Jetson 직렬 통신 프로토콜 라이브러리이고, `esp32/motor/esp32_motor_comm/`·`esp32/sensor/esp32_sensor_comm/`이 그 위에서 도는 통신 계층 스케치다(S15P11A301-84). 모터 스케치는 BTS7960 2개(후륜 구동)와 전륜 조향 서보 PWM 1채널을 함께 내보낸다(`steering.cpp`, S15P11A301-297). 나머지 `*_test` 스케치는 벤치 테스트용으로 그대로 유지된다. Jetson 쪽 상대 코드는 `jetson/ros2_ws/src/esp32_bridge/`에 있다.

모터, 드라이버, 배터리 TBD가 확정되면 측정값, 선택 근거, 담당자와 결정일을 기록합니다.

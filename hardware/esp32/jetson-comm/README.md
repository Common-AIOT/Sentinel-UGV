# jetson-comm

모터 ESP32와 센서 ESP32 스케치가 공유하는 순수 프로토콜 라이브러리다. `Arduino.h`에 의존하지 않으므로 호스트 `g++`로도 빌드/테스트할 수 있다.

프레이밍·메시지 타입·워치독 정책의 전체 설계는 `docs/03-제어-캘리브레이션.md` 34장을 따른다. 이 라이브러리는 그중 통신 계층(§34-5)만 구현한다.

## 파일

- `message_ids.h` — 메시지 코드(0x01~0x30), `PROTOCOL_VERSION`, `MAX_PAYLOAD_BYTES`.
- `fault_codes.h` — §34-9 fault bit 13개.
- `protocol.h` / `protocol.cpp` — 프레임 헤더, CRC-16/CCITT-FALSE, COBS 인코딩/디코딩, `buildFrame`/`parseFrame`, 메시지별 pack/unpack.
- `test_vectors/` — CRC16·COBS 검증 벡터(사람이 읽는 근거 자료). 실행 가능한 테스트는 `test/test_protocol.cpp`와 `jetson/ros2_ws/src/esp32_bridge/test/test_packet_codec.py`에 동일 값이 하드코딩되어 있다.
- `test/test_protocol.cpp` — 호스트 컴파일 유닛테스트.

## 스케치에서 사용하는 법

```cpp
#include "../../jetson-comm/protocol.h"
```

## 신규 페이로드 (문서에 없던 것, 이번 작업에서 확정)

`HELLO_ACK`, `DIAGNOSTIC`, `COMMAND_ACK`, `CONFIG`(0x30, GET/SET 겸용)의 페이로드 구조는 `docs/03-제어-캘리브레이션.md` §34-5에 없어 이번 작업에서 새로 정의했다(`protocol.h`의 구조체 주석 참고). 문서에 addendum으로 반영 필요.

## 호스트 테스트 빌드

```sh
g++ -std=c++17 -I. test/test_protocol.cpp protocol.cpp -o test_protocol
./test_protocol
```

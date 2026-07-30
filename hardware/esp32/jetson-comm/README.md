# jetson-comm

모터 ESP32와 센서 ESP32 스케치가 공유하는 순수 프로토콜 라이브러리다. `Arduino.h`에 의존하지 않으므로 호스트 `g++`로도 빌드/테스트할 수 있다.

프레이밍·메시지 타입·워치독 정책의 전체 설계는 `docs/03-제어-캘리브레이션.md` 34장을 따른다. 이 라이브러리는 그중 통신 계층(§34-5)만 구현한다.

**Arduino 라이브러리 형태다** (`library.properties` + `src/`). Arduino IDE는 스케치 폴더 밖의 `.cpp`를 상대경로 include만으로는 컴파일해주지 않으므로(헤더는 찾지만 링크 단계에서 undefined reference 발생), 실제 사용 전에 아래 "설치" 절차가 필요하다.

## 파일

- `library.properties` — Arduino 라이브러리 메타데이터.
- `src/message_ids.h` — 메시지 코드(0x01~0x30), `PROTOCOL_VERSION`, `MAX_PAYLOAD_BYTES`.
- `src/fault_codes.h` — §34-9 fault bit 13개.
- `src/protocol.h` / `src/protocol.cpp` — 프레임 헤더, CRC-16/CCITT-FALSE, COBS 인코딩/디코딩, `buildFrame`/`parseFrame`, 메시지별 pack/unpack.
- `test_vectors/` — CRC16·COBS 검증 벡터(사람이 읽는 근거 자료). 실행 가능한 테스트는 `test/test_protocol.cpp`와 `jetson/ros2_ws/src/esp32_bridge/test/test_packet_codec.py`에 동일 값이 하드코딩되어 있다.
- `test/test_protocol.cpp` — 호스트 컴파일 유닛테스트.

## 설치 (Arduino IDE가 이 라이브러리를 찾게 하기)

Arduino IDE는 스케치북의 `libraries/` 아래에 있는 폴더만 라이브러리로 인식한다. 이 저장소 경로를 단일 진실 공급원으로 유지하면서 IDE가 찾게 하려면, 스케치북 `libraries/` 안에 이 폴더를 가리키는 디렉터리 정션(Windows, 관리자 권한 불필요)을 만든다.

```powershell
# 스케치북 위치는 Arduino IDE File > Preferences의 "Sketchbook location" 확인.
# 기본값은 보통 Documents\Arduino 다.
mklink /J "%UserProfile%\Documents\Arduino\libraries\jetson_comm" "C:\Users\SSAFY\workspace\S15P11A301\hardware\esp32\jetson-comm"
```

정션을 만든 뒤 Arduino IDE를 재시작하면 `esp32_motor_comm`/`esp32_sensor_comm` 스케치가 `#include <protocol.h>`로 이 라이브러리를 찾아 `src/*.cpp`까지 함께 컴파일·링크한다. 심볼릭 링크/정션을 만들 수 없는 환경이라면 이 폴더를 통째로 복사해도 되지만, 그 경우 코드를 고칠 때마다 다시 복사해야 한다는 점을 유의할 것.

## 스케치에서 사용하는 법

```cpp
#include <protocol.h>
#include <fault_codes.h>
```

## 신규 페이로드 (문서에 없던 것, 이번 작업에서 확정)

`HELLO_ACK`, `DIAGNOSTIC`, `COMMAND_ACK`, `CONFIG`(0x30, GET/SET 겸용)의 페이로드 구조는 `docs/03-제어-캘리브레이션.md` §34-5에 없어 이번 작업에서 새로 정의했다(`protocol.h`의 구조체 주석 참고). 문서에 addendum으로 반영 필요.

## 호스트 테스트 빌드

```sh
cd test
g++ -std=c++17 -I../src test_protocol.cpp ../src/protocol.cpp -o test_protocol
./test_protocol
```

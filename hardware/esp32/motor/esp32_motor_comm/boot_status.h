// 부팅 후 5초간 폰 핫스팟 접속 상태와 접속 주소를 보여준다 (S15P11A301-312).
//
// **Jetson 전용 UART(Serial, 921600)는 절대 쓰지 않는다.** README `## 디버그
// 주의` 가 어느 파일에도 `Serial.print()` 를 금지하는 이유는 그 UART가 COBS+CRC16
// 바이너리 프레이밍 전용이라 텍스트가 섞이면 CRC 드롭으로 복구는 되어도 노이즈·
// 오탐 카운트를 유발하기 때문이다. 그래서 이 창은 **별도 물리 UART(Serial2,
// GPIO16=RX2·GPIO17=TX2)**를 쓴다 - 모터·서보 어느 핀과도 겹치지 않는다.
//
// WROOM-32 온보드 USB-UART 브리지는 UART0(Jetson용) 하나뿐이다. 이 상태 표시를
// 보려면 **별도 USB-UART 어댑터**를 GPIO16·GPIO17·GND 에 물려야 한다.
//
// **comm_task 의 시작을 조금도 지연시키지 않는다.** 이 모듈은 별도 UART에 값만
// 쓸 뿐이고 어떤 태스크의 타이밍도 바꾸지 않는다 - Jetson 핸드셰이크는
// esp32_motor_comm.ino 55-57 이 이미 강제하는 비블로킹 원칙대로 항상 즉시
// 시작한다. "5초 뒤 젯슨과 통신 시작" 은 이 모듈이 하는 일이 아니다 - comm_task
// 는 이 창과 무관하게 부팅과 동시에 이미 돌고 있다.
#pragma once

// Serial2 시작과 창 시작 시각 기록. setup() 의 맨 앞, 다른 초기화보다 먼저
// 호출한다 - 5초 창을 최대한 실제 전원 인가 시점에 맞추기 위해서다.
//
// 컴파일 타임에 끄려면 빌드 정의에 `ENABLE_BOOT_STATUS_SERIAL2=0` 을 추가한다
// (README `## 디버그 주의` 가 요구하는 "컴파일 타임 매크로 게이트").
void bootStatusInit();

// manualWebTaskFn 루프에서 매 반복 호출한다. 5초가 지나면 스스로 닫히고 그
// 이후로는 값을 만들 필요조차 없다 - 호출 전에 bootStatusActive() 로 먼저
// 확인해 IP 문자열 조립 같은 불필요한 작업을 건너뛴다.
//
// connected: 지금 WiFi 연결 여부. ip: 연결됐을 때의 로컬 IP 문자열(미연결이면
// 빈 문자열). mdnsHost: mDNS 등록에 성공했을 때의 호스트명(미등록이면 빈 문자열,
// ".local" 은 붙이지 않은 이름 그대로 넘긴다).
void bootStatusUpdate(bool connected, const char* ip, const char* mdnsHost);

// 창이 아직 열려 있는가. false 면 bootStatusUpdate 를 불러도 아무 일도 하지
// 않으므로, 호출자는 이 값으로 매 루프의 문자열 조립 비용을 아낄 수 있다.
bool bootStatusActive();

// 모터 ESP32 공유 상태. comm_task/control_task 양쪽에서 뮤텍스로 보호된 채 접근한다.
#pragma once

#include <cstdint>
#include <functional>

// docs/03-제어-캘리브레이션.md §34-9
enum class MotorBoardState : uint8_t {
  BOOT,
  SAFE_IDLE,
  READY,
  MANUAL_ACTIVE,
  AUTO_ACTIVE,
  STOPPING,
  ESTOP_LATCHED,
  FAULT_LATCHED,
};

struct MotorSharedState {
  // §34-6: 부팅 직후 상태는 SAFE_IDLE.
  MotorBoardState state = MotorBoardState::SAFE_IDLE;
  uint16_t faultFlags = 0;

  // 가장 최근에 수신한 DRIVE_COMMAND 타깃. 실제 액추에이션은 safety_stub.h(후륜
  // 구동)와 steering.h(전륜 조향 서보)가 담당하고, 여기에는 통신 계층 값만 보관한다.
  // 후륜은 전·후진 전용이라 좌·우가 같은 값이며(§34-2), 조향은 별도 필드다.
  int16_t targetDriveLeftMmps = 0;
  int16_t targetDriveRightMmps = 0;
  // Jetson이 보낸 원본 조향 목표. 실제 추종 중인 값(클램프·슬루레이트 적용 후)은
  // steeringTargetMdeg()가 갖고 있으며 DRIVE_STATE는 그쪽을 보고한다.
  int16_t targetSteeringMdeg = 0;
  uint16_t maxSteeringRateMdps = 0;
  uint16_t commandTimeoutMs = 300;

  // 링크 생존만 측정한다. **액추에이션 권한과 무관하다** (S15P11A301-298).
  //
  // 수동이 중재에서 이겼다는 이유로 300ms 워치독이 트립하면 안 된다 - 젯슨 링크는
  // 멀쩡한데 STOPPING 으로 떨어져 수동 주행이 끊긴다. 그래서 액추에이션을 거부한
  // DRIVE_COMMAND 도 이 타임스탬프는 갱신한다.
  uint32_t lastValidDriveCommandMs = 0;
  uint16_t lastAcceptedSequence = 0;
  bool hasAcceptedSequence = false;

  // 실제로 **액추에이션한** 마지막 시퀀스. `DRIVE_STATE.appliedSequence` 가 이 값을
  // 보고한다. `lastAcceptedSequence` 와 나눈 이유는 수동 래치 중에 거부한 명령을
  // "반영했다" 고 거짓말하지 않기 위해서다.
  uint16_t lastAppliedSequence = 0;
  bool hasAppliedSequence = false;

  // ---- 수동 채널 (S15P11A301-298) ----
  //
  // 폰이 자기 핫스팟 위에서 이 보드의 HTTP 엔드포인트에 직결한다. 젯슨은 폰에
  // 도달할 수 없으므로 **액추에이션 중재자는 이 보드**이며, 젯슨은 DRIVE_STATE 를
  // 따라간다. 아래 필드가 그 중재의 전부다.

  // 수동이 액추에이션 권한을 쥐고 있는가. 이것이 참인 동안 젯슨의 DRIVE_COMMAND 는
  // 기록만 되고 바퀴에 닿지 않는다.
  //
  // **푸는 길은 SET_MODE(AUTO) 하나뿐이다.** 모바일 「정지」·deadman 해제·수동 TTL
  // 만료·WiFi 끊김·STOP_COMMAND·젯슨 재접속(HELLO)은 전부 바퀴만 0 으로 만들고 이
  // 값을 건드리지 않는다. 예외는 ESTOP 뿐이며 그것은 권한을 막는 게 아니라 벗긴다.
  bool manualLatched = false;
  // 관제·초음파 중계의 STOP_COMMAND 를 받았다. 손을 뗐다가 다시 눌러야 움직인다.
  //
  // 무조건 잠그지 않는 이유: 전륜 조향에서 정지 구역 탈출은 후진뿐이고 후방 센싱이
  // 없다(04장 950-953). 잠그면 로봇을 들어 옮기는 것 말고 복구 경로가 없다.
  bool manualReArmRequired = false;
  bool manualDeadman = false;

  // 폰이 발급받은 로컬 세션. 관제의 controlSessionId 가 **아니다** - 폰은 자기
  // 핫스팟에서 Spring 에 도달할 수 없다. 단일 조종자만 강제하며 신원은 아니다.
  uint32_t manualSessionId = 0;
  uint16_t manualSequence = 0;
  bool hasManualSequence = false;

  uint32_t lastManualInputMs = 0;
  bool hasManualInput = false;
  uint16_t manualTtlMs = 250;

  int16_t manualDriveMmps = 0;
  int16_t manualSteeringMdeg = 0;
  // 이 틱에 조향을 걸어도 되는가. **미리 걸러 둔다** - |v| 가 작을 때 조향을
  // 시도하면 steering.cpp 가 거부하고 그것이 FAULT_STEERING_COMMAND_INVALID 로
  // 올라가, 정지 중 좌우 누름마다 bit 14 가 깜박여 그 비트의 의미("젯슨이 무효한
  // 것을 보냈다")가 파괴된다.
  bool manualSteeringRequested = false;

  // 승격 판정용 연속 패킷 run. 100ms·2패킷을 채워야 래치가 걸린다.
  bool manualRunActive = false;
  uint8_t manualRunPackets = 0;
  uint32_t manualRunStartedMs = 0;

  // 수동 래치 중 거부한 젯슨 명령 수. 진단용이며 CTRL-29 가 증가를 확인한다.
  uint32_t jetsonRefusedCount = 0;
  // 거부 ACK 엣지 게이팅. 50Hz REJECTED 홍수를 막는다.
  bool jetsonRejectAcked = false;
  // stale-sequence ACK 레이트리밋.
  uint32_t lastStaleAckMs = 0;
  bool hasStaleAck = false;

  // 진단용 누적 카운터 (DIAGNOSTIC 페이로드로 그대로 보고된다)
  uint32_t crcErrorCount = 0;
  uint32_t droppedFrameCount = 0;
  uint32_t staleSequenceCount = 0;
};

void motorSharedStateInit();
MotorSharedState motorSharedStateSnapshot();
void motorSharedStateUpdate(const std::function<void(MotorSharedState&)>& mutator);

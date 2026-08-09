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
  // **세우는 것은 관제의 SET_MODE(MANUAL) 하나뿐이다** (S15P11A301-345). 폰 입력은
  // 이 값을 세우지 못하며, 거짓인 동안 폰 패킷은 장부에만 기록되고 바퀴에 닿지
  // 않는다. 유일한 예외가 아래 `manualFallbackLatched` 의 링크 침묵 폴백이다.
  //
  // **푸는 길은 SET_MODE(AUTO) 하나뿐이다.** 모바일 「정지」·deadman 해제·수동 TTL
  // 만료·WiFi 끊김·STOP_COMMAND·젯슨 재접속(HELLO)은 전부 바퀴만 0 으로 만들고 이
  // 값을 건드리지 않는다. 예외는 ESTOP 뿐이며 그것은 권한을 막는 게 아니라 벗긴다.
  bool manualLatched = false;
  // 위 래치가 **관제 승인이 아니라 링크 침묵 폴백으로** 걸렸다 (S15P11A301-345).
  //
  // 순간 플래그가 아니라 래치다. 폴백은 정의상 젯슨이 침묵하는 동안 발동하므로,
  // 발동 순간의 DRIVE_STATE 는 아무도 받지 못한다. 링크가 살아난 뒤 관제가
  // 「그때 폰이 권한을 가져갔다」를 볼 수 있어야 하므로 사실을 붙들고 있다가
  // `DRIVE_STATE.authorityFlags` 로 계속 보고한다.
  //
  // 내려가는 경로도 하나뿐이다 - `SET_MODE(AUTO)` 수락. 그것이 관제가 권한을
  // 되찾는 순간이고, 곧 관제가 이 사실을 확인했다는 유일한 증거다. **ESTOP 은
  // 내리지 않는다** - E-Stop 이 수동 권한을 벗기는 것과 「폴백이 발동했었다」는
  // 사실은 다른 층이고, 여기서 지우면 정전 구간의 발동이 영원히 사라진다.
  bool manualFallbackLatched = false;
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

  // 연속 패킷 run. 100ms·2패킷이 「페이지가 열려만 있는가, 사람이 실제로 조종
  // 중인가」를 가른다. S15P11A301-345 이후 이 판정이 래치를 거는 곳은 링크 침묵
  // 폴백 하나뿐이다.
  bool manualRunActive = false;
  uint8_t manualRunPackets = 0;
  uint32_t manualRunStartedMs = 0;

  // ---- 조향 캘리브레이션 (S15P11A301-312, drive_test 벤치 기능 이식) ----
  //
  // §35-3 「조향 중립·각도 매핑」 실측이 아직 TBD-HW-008 이라 세 값이 전부 임시값이다.
  // 벤치에서 폰 UI 로 돌려 보고 확정하기 위한 런타임 창구이며, **확정되면
  // steering.cpp 의 상수로 굳히고 이 경로는 조정용으로만 남는다.**
  //
  // **재부팅하면 전부 기본값으로 돌아간다.** 영속화하지 않는 것은 의도다 - 잘못
  // 맞춘 중립이 플래시에 남아 다음 부팅의 §34-6 중립 초기화를 오염시키면, 그때는
  // 아무도 그것이 왜 틀어졌는지 찾지 못한다.
  //
  // 쓰기는 manual_web 이 게이팅한다(자율 주행 중·바퀴 구동 중·래치 중 거부).
  // 실제 반영은 언제나 control_task 의 10ms 틱 하나뿐이다.
  // 부팅값은 false다. setup()이 모든 주변장치 초기화를 끝낸 뒤 true로 바꾸며, 그 전에는
  // 외부 10kΩ pull-down과 LEDC duty 0이 GPIO18을 LOW로 유지한다.
  bool servoArmed = false;         // 서보 PWM 출력. false 면 펄스를 끊어 서보가 free 가 된다
  uint8_t servoCenterDeg = 145;    // 서보 중립 각도. steering.cpp SERVO_CENTER_DEG 와 같은 기본값
  uint8_t servoMaxOffsetDeg = 30;  // 좌우 최대 오프셋. δ_max 가 이 각도로 매핑된다

  // 캘리브레이션 조그 1회 요청. control_task 가 소비하며 §34-2 정지 중 조향 금지를
  // 의도적으로 우회한다 - 바퀴를 띄운 벤치에서 엔드포인트를 재는 것이 유일한 용도다.
  bool servoJogPending = false;
  int16_t servoJogMdeg = 0;

  // 수동 주행 최대 속도(%). 보드가 최종 권한을 갖는다 - 폰이 새로고침되거나 다른
  // 조종자가 붙어도 이 상한은 유지된다. 100 이면 MANUAL_MAX_DRIVE_MMPS 그대로다.
  uint8_t manualSpeedLimitPercent = 100;

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

  // ---- 링크 접촉 (S15P11A301-321, 센서 board_state.h의 lastValidJetsonRxMs와 같은
  // 이름·같은 뜻) ----
  //
  // `lastValidDriveCommandMs`(위)와 축이 다르다. 저건 "액추에이션 안전 정지"용이라
  // DRIVE_COMMAND만 갱신해야 하고(그래야 mode_arbiter가 "상위 파이프라인이 멈췄다"를
  // 정확히 본다), 이건 "물리 링크가 살아있나"용이라 HELLO를 포함해 Jetson에서 온
  // 어떤 유효 프레임이든 갱신한다. 두 시각을 하나로 합치면 keepalive HELLO가
  // DRIVE_COMMAND 부재를 가려 안전 정지가 늦어진다 - 절대 합치지 말 것.
  uint32_t lastValidJetsonRxMs = 0;
  bool hasReceivedFromJetson = false;
};

void motorSharedStateInit();
MotorSharedState motorSharedStateSnapshot();
void motorSharedStateUpdate(const std::function<void(MotorSharedState&)>& mutator);

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

  uint32_t lastValidDriveCommandMs = 0;
  uint16_t lastAcceptedSequence = 0;
  bool hasAcceptedSequence = false;

  // 진단용 누적 카운터 (DIAGNOSTIC 페이로드로 그대로 보고된다)
  uint32_t crcErrorCount = 0;
  uint32_t droppedFrameCount = 0;
  uint32_t staleSequenceCount = 0;
};

void motorSharedStateInit();
MotorSharedState motorSharedStateSnapshot();
void motorSharedStateUpdate(const std::function<void(MotorSharedState&)>& mutator);

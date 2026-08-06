// 전륜 조향 서보(DS51150-12V) 액추에이션 계층 (docs/03-제어-캘리브레이션.md §34-2·34-7·34-8).
//
// 2026-08-06 하드웨어 변경으로 전륜 조향이 복구되었다. 조향은 이 계층이 전담하고
// 후륜 BTS7960 2개는 전·후진만 담당한다(safety_stub.h).
//
// 이 계층이 하는 일은 셋뿐이다(§34-8: "조향에는 제어 루프가 없다").
//   1. 목표 조향각(밀리도)을 ±δ_max로 클램프한다.
//   2. 변화율을 max_steering_rate_mdps로 제한한다(슬루레이트).
//   3. 각도를 서보 펄스폭으로 매핑해 50Hz PWM으로 내보낸다.
// 서보가 내부 폐루프로 각도를 유지하고 외부 피드백이 없으므로 오차를 줄일 수단이
// 없다 - **매핑 정확도가 곧 조향 정확도**이며 실측 표는 §35-3에서 채운다.
//
// 정지·워치독·STOP/ESTOP 경로에서 조향각을 중립으로 되돌리지 않는다(§34-7). 관성
// 주행 중 중립으로 꺾으면 의도한 궤적을 벗어나기 때문이며, 중립으로 가는 예외는
// 부팅 직후 하나뿐이다(믿을 수 있는 마지막 값이 없다, §34-6).
#pragma once

#include <cstdint>

// LEDC 채널·타이머 attach와 부팅 중립(δ=0) 초기화. setup()에서 태스크 생성 전에
// 한 번 호출한다. 구동 PWM(20kHz)과 주파수 대역이 달라 별도 타이머를 쓴다(§34-1).
void steeringInit();

// 새 DRIVE_COMMAND의 조향 목표를 반영한다. 반환값은 이 명령이 그대로 수락됐는지
// 여부이며, false면 호출자가 FAULT_STEERING_COMMAND_INVALID를 세운다(§34-9 bit 14).
//
// driveTargetMmps는 같은 명령의 후륜 목표 속도 중 절댓값이 큰 쪽이다. abs(v)가
// v_min보다 작은데 조향각을 바꾸려는 명령은 **거부하고 마지막 목표를 유지한다**
// (§34-2: 정지 중 조향은 회두를 만들지 못하고 타이어·링키지·서보에만 부담이다).
bool steeringSetTarget(int16_t requestedMdeg, uint16_t maxRateMdps, int16_t driveTargetMmps);

// 슬루레이트를 적용해 서보 펄스를 갱신한다. control_task가 100Hz로 호출한다.
void steeringUpdate(uint32_t nowMs);

// DRIVE_STATE 보고용(§34-5). target은 슬루레이트 제한 후 실제 추종 중인 목표,
// actuatorCmd는 서보에 내보낸 펄스폭(µs)이다.
int16_t steeringTargetMdeg();
int16_t steeringActuatorCmdUs();

// ±δ_max(밀리도). 상위 계층 클램프와 값을 맞추기 위해 노출한다.
int16_t steeringMaxMdeg();

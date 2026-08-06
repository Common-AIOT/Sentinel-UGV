// 조향 한계값. `steering.cpp` 와 `mode_arbiter.cpp` 가 **같은 값**을 봐야 하므로
// 별도 헤더로 뺐다 (S15P11A301-298).
//
// 수동 매핑(`ang` −1000..1000 → 밀리도)과 서보 클램프가 갈라지면 폰의 조향 슬라이더
// 끝이 실제 δ_max 와 어긋난다 — 화면에서는 보이지 않고 조향이 덜 먹히는 것으로만
// 드러난다. 정지 중 조향 금지 임계도 마찬가지다. 수동 경로가 이 값을 모르면
// `steering.cpp` 가 거부하는 명령을 계속 보내게 되고, 그 거부는
// `FAULT_STEERING_COMMAND_INVALID`(bit 14)로 올라가 그 비트의 의미를 파괴한다.
//
// `Arduino.h` 를 포함하지 않으므로 호스트 g++ 테스트에서도 그대로 쓴다.
#pragma once

#include <cstdint>

// 앞바퀴 실제 조향각(δ) 기준 한계. δ_max 실측(TBD-HW-008) 전에는 서보 엔드포인트와
// 1:1 로 두고, 실측 후 이 값만 바꾸면 서보 게인이 자동으로 따라온다.
//
// Jetson `vehicle_kinematics` 의 `max_steering_rad` 와 같은 값이어야 한다 — 어긋나면
// Jetson 이 보낸 명령을 펌웨어가 조용히 클램프하는 구간이 생긴다.
constexpr int16_t STEERING_MAX_MDEG = 30000;  // 30.000°

// 후륜 목표 속도가 이 값보다 작으면 조향 목표 변경을 거부한다(§34-2). Jetson
// `vehicle_kinematics` 의 `min_linear_mps`(0.03m/s)와 같은 값이다.
constexpr int16_t STEERING_MIN_DRIVE_MMPS = 30;

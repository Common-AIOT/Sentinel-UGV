"""최종 안전 게이트 판정 (S15P11A301-237, 명세 24.1·34-7).

체인의 마지막 층이다. 위층(collision_monitor)이 통과시킨 속도를 받아 **낼지 0으로
막을지**만 결정한다. rclpy 없이 시험한다.

## 침묵은 "이상 없음"이 아니다

이 모듈의 설계 전체가 이 한 가지에서 나온다. 감시 대상 셋(상위 명령·임무 상태·
초음파 protective_stop)은 모두 **토픽이 조용해질 수 있고**, 그 침묵은 세 가지
전혀 다른 뜻일 수 있다.

    1. 아직 아무도 발행하지 않았다      (기동 직후)
    2. 발행자가 죽었다                   (노드 사망·USB 재열거)
    3. 값이 안 바뀌어서 안 보낸다        (있어서는 안 되지만 실제로 겪었다)

셋을 구별할 방법이 없으므로 **전부 정지로 다룬다.** 2026-08-04 에 온습도 센서를
빼자 `/environment/*` 가 조용해진 것을 확인했고(S15P11A301-258), 같은 보드가
`/proximity/protective_stop` 을 낸다 — 초음파가 죽으면 "장애물 없음" 과 똑같이
조용하다. 그것을 통과로 읽으면 보호가 없는데 있다고 믿는 상태가 된다.

## 왜 0을 계속 내는가

막을 때 발행을 멈추지 않고 **0 을 계속 낸다.** 하류 `vehicle_kinematics` 는
`/cmd_vel` 이 300ms 끊기면 자기 판단으로 정지 명령을 내는데(S15P11A301-234),
그러면 "게이트가 막았다" 와 "게이트가 죽었다" 가 화면에서 같아진다. 0 을 계속
내면 발행자 수와 주기로 게이트가 살아 있음이 보이고, 정지 이유도 함께 낼 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 34-7 이중 watchdog. Jetson 쪽 상한이 300ms 다 — ESP32 모터 watchdog 과 같은 값을
# 쓴다. 여기가 더 길면 ESP32 가 먼저 끊어 이 층이 무의미해지고, 더 짧으면 정상
# 주행 중에도 툭툭 끊긴다.
DEFAULT_COMMAND_TTL_S = 0.3

# 임무 상태는 latched(transient_local) 로 오고 상태가 바뀔 때만 발행된다. 즉
# 정상 주행 중에도 몇 분간 조용할 수 있어서 명령과 같은 TTL 을 쓸 수 없다.
# 그래도 무한정 신뢰하지는 않는다 — mission_manager 가 죽은 것을 알아야 한다.
DEFAULT_MISSION_TTL_S = 10.0

# 초음파는 esp32_sensor_bridge 가 keepalive 주기(0.15s)로 상태를 재전송하므로
# 이보다 훨씬 촘촘히 온다. 넉넉히 잡아도 보드가 빠지면 확실히 걸린다.
DEFAULT_PROXIMITY_TTL_S = 1.0

# `/scan` 도 여기서 본다. `nav2_collision_monitor` 는 소스가 `source_timeout` 을
# 넘으면 그 소스를 **쓰지 않고 넘어간다** — 라이다가 죽으면 "장애물 없음" 과
# 똑같이 통과시킨다. 그 층에 기대면 LiDAR 사망이 보호 해제가 되므로, 마지막
# 관문에서 신선도를 직접 본다(README 의 "센서 오류 안전 정지"가 이 층의 몫이다).
# X4 Pro 는 10Hz 라 0.5s 면 5스캔을 놓친 것이다.
DEFAULT_SCAN_TTL_S = 0.5

# 이 상태에서는 어떤 속도도 내보내지 않는다 (26.2 movement_allowed).
# SAFE_IDLE 은 "명령을 기다리는 중" 이고 PAUSED·ESTOP·ERROR 는 사람이 풀어야 한다.
#
# **MANUAL 은 일부러 넣지 않는다** (S15P11A301-298). 이 집합의 뜻은 "사람이 풀어야
# 한다" 인데 MANUAL 은 다르다 — 젯슨에 권한이 없을 뿐이고, 그 사실은 이미
# `movement_allowed=false`(`MOVEMENT[MANUAL]`)로 표현돼 같은 0 을 만든다. 넣으면
# 두 가지를 잃는다. (1) `mux.py` 가 남긴 `/cmd_vel_manual` 경로를 조용히 영구
# 봉쇄한다 — 언젠가 그 토픽에 발행자가 생겨도 이 층이 무조건 막는다. (2) 정지
# 사유가 뭉개진다. 두 집합을 구분해 두면 로그에서 `MOVEMENT_NOT_ALLOWED` 만 뜨는
# MANUAL 과 둘 다 뜨는 PAUSED 가 갈려 운영자가 *어느 층이* 막았는지 알 수 있다.
BLOCKING_MISSION_STATES = frozenset({
    'SAFE_IDLE',
    'PAUSED',
    'ESTOP',
    'ERROR',
    'COMPLETED',
})


@dataclass(frozen=True)
class GateInputs:
    """게이트가 보는 전부. 시각은 단조 시계 초(monotonic)다."""

    now_s: float
    linear_mps: float
    angular_radps: float
    command_stamp_s: float | None = None
    mission_state: str | None = None
    mission_stamp_s: float | None = None
    movement_allowed: bool | None = None
    protective_stop: bool | None = None
    protective_stamp_s: float | None = None
    scan_stamp_s: float | None = None


@dataclass(frozen=True)
class GateTimeouts:
    command_ttl_s: float = DEFAULT_COMMAND_TTL_S
    mission_ttl_s: float = DEFAULT_MISSION_TTL_S
    proximity_ttl_s: float = DEFAULT_PROXIMITY_TTL_S
    scan_ttl_s: float = DEFAULT_SCAN_TTL_S


@dataclass(frozen=True)
class GateDecision:
    """판정 결과. `reasons` 가 비어 있으면 통과다."""

    linear_mps: float
    angular_radps: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)


def _stale(now_s: float, stamp_s: float | None, ttl_s: float) -> bool:
    """`None`(한 번도 안 옴)도 낡음으로 다룬다 — 침묵은 이상 없음이 아니다."""
    if stamp_s is None:
        return True
    return (now_s - stamp_s) > ttl_s


def evaluate(inputs: GateInputs, timeouts: GateTimeouts | None = None) -> GateDecision:
    """막을 이유를 **전부** 모아서 낸다.

    첫 이유에서 멈추지 않는 것은 의도다. 초음파도 걸리고 임무 상태도 PAUSED 인
    상황에서 하나만 로그에 남으면, 그것을 고친 뒤에도 안 움직이는 이유를 다시
    처음부터 찾는다.
    """
    limits = timeouts or GateTimeouts()
    reasons: list[str] = []

    if _stale(inputs.now_s, inputs.command_stamp_s, limits.command_ttl_s):
        reasons.append(
            'COMMAND_STALE: 상위 명령이 '
            f'{limits.command_ttl_s * 1000:.0f}ms 이상 없다 (Nav2·탐사·수동 확인)'
        )

    if _stale(inputs.now_s, inputs.mission_stamp_s, limits.mission_ttl_s):
        reasons.append(
            'MISSION_STALE: 임무 상태가 '
            f'{limits.mission_ttl_s:.0f}s 이상 없다 (mission_manager 확인)'
        )
    else:
        if inputs.mission_state in BLOCKING_MISSION_STATES:
            reasons.append(f'MISSION_STATE: {inputs.mission_state} 에서는 주행하지 않는다')
        # movement_allowed 가 없으면(None) 판단 근거가 없다는 뜻이므로 막는다.
        if inputs.movement_allowed is not True:
            reasons.append('MOVEMENT_NOT_ALLOWED: movementAllowed 가 참이 아니다')

    if _stale(inputs.now_s, inputs.protective_stamp_s, limits.proximity_ttl_s):
        reasons.append(
            'PROXIMITY_STALE: 초음파 보호정지 신호가 '
            f'{limits.proximity_ttl_s:.0f}s 이상 없다 — 센서 보드를 확인하라. '
            '침묵을 "장애물 없음" 으로 읽지 않는다'
        )
    elif inputs.protective_stop:
        reasons.append('PROTECTIVE_STOP: 초음파 임계 거리 — Nav2 목표와 무관하게 정지')

    if _stale(inputs.now_s, inputs.scan_stamp_s, limits.scan_ttl_s):
        reasons.append(
            f'SCAN_STALE: /scan 이 {limits.scan_ttl_s * 1000:.0f}ms 이상 없다 — '
            'collision_monitor 는 낡은 소스를 그냥 건너뛰므로 이 층이 막는다'
        )

    if reasons:
        return GateDecision(0.0, 0.0, tuple(reasons))
    return GateDecision(inputs.linear_mps, inputs.angular_radps)

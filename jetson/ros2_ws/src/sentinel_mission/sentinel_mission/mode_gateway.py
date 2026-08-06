"""운영자의 모드 전환 의도를 모터 보드에 물어보고 답을 기다리는 계층
(S15P11A301-298, 명세 26.1·14.2).

`rclpy` 를 import 하지 않는 순수 로직이라 ROS 없이 pytest 로 검증한다
(`sentinel_safety/gate.py`·`mux.py` 와 같은 패턴).

## 왜 상태기계 바깥인가

`MissionStateMachine` 은 **동기 함수**다. 신호가 들어오면 그 자리에서 전이를 돌려
준다. 그런데 모드 전환은 시리얼 왕복이 있다 — 「자율」 버튼은 모터 ESP32 가
**거부할 수 있는 유일한 명령**이고(최근 500ms 안에 모바일 수동 입력이 있었으면
거부한다), 보드가 대답하지 않을 수도 있다. 비동기 왕복·타임아웃·거부 코드 분류를
상태기계에 넣으면 그 클래스가 시간과 전송을 알게 된다.

그래서 역할을 이렇게 나눈다.

    운영자 의도 → 보드 (왕복·타임아웃·거부 분류)   ModeGateway   ← 이 파일
    보드가 알린 사실 → 임무 상태 전이               MissionStateMachine
    프레임 패킹                                     esp32_bridge (중계만)

`MANUAL_REQUESTED`/`AUTO_REQUESTED` 는 상태기계에 닿기 **전에** 노드가 여기로
돌린다. 여기가 보드 확인을 마치면 `MANUAL_ENGAGED`/`AUTO_ENGAGED` 라는 **사실**로
바꿔 상태기계에 넣는다.

## 시각은 단조 시계 초다

TTL·타임아웃을 재므로 벽시계를 쓰면 NTP 보정 한 번에 500ms 창이 뒤틀린다.
`gate.py` 와 같은 규약으로 `time.monotonic()` 초를 받는다. 상태기계에 넘기는
`datetime` 과는 다른 시계이며 섞지 않는다.

## commandId 당 최종 ACK 정확히 하나

중간 `ACCEPTED` 를 보내고 나중에 최종 ack 를 보내면 안 된다. 백엔드
`CommandAckWriter.write` 는 맹목적 UPDATE 라 나중 것이 앞의 것을 덮고, 관제
`RobotContext.watchCommand` 는 `ACCEPTED` 에서 조용히 반환해 두 번째를 보지
못한다. 그래서 한 명령은 정확히 한 번 `EXECUTED` 또는 `REJECTED` 로 끝난다.

## 상관은 시퀀스가 아니라 "동시 1건 이하"로 한다

`esp32_motor_bridge_node._send_frame` 이 시퀀스를 내부에서 할당하고 노출하지
않으므로 `acked_sequence` 로 요청과 ACK 를 이을 수 없다. 대신 이 클래스가 요청을
직렬화한다 — 답을 기다리는 동안 두 번째 요청이 오면 거부한다. `SET_MODE` 는
누름당 프레임 하나이고 재전송하지 않으므로(멱등이 아니다: 3프레임 → 3ACK →
3전이) 이 규칙으로 충분하다. 손실은 500ms 타임아웃과 운영자 재시도가 덮는다.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from .mission_state import (
    ACK_EXECUTED,
    ACK_REJECTED,
    REASON_ERROR_LATCHED,
    REASON_ESTOP_ACTIVE,
    REASON_INVALID_STATE,
    REASON_MANUAL_INPUT_ACTIVE,
    REASON_MOTOR_BOARD_NO_ACK,
    REASON_MOTOR_BOARD_REJECTED,
    MissionState,
    Signal,
)

# ---- 요청할 수 있는 모드. `SET_MODE(SAFE_IDLE)` 은 보내는 쪽이 없다(계획 D11). ----
MODE_MANUAL = 'MANUAL'
MODE_AUTO = 'AUTO'
REQUESTABLE_MODES = frozenset({MODE_MANUAL, MODE_AUTO})

# 보드가 수동을 래치했다고 인정하기까지 필요한 지속 시간.
#
# **모바일 deadman 100ms 와 다른 타이머다.** 그쪽은 폰에 있고 "사람이 정말 누르고
# 있는가" 를 본다. 이쪽은 젯슨에 있고 50Hz `DRIVE_STATE` 다섯 프레임을 요구해
# **손상된 단일 프레임**을 견딘다. 두 필터는 독립이며 값이 같은 것은 우연이다.
MANUAL_CONFIRM_SECONDS = 0.10

# `DRIVE_STATE` 가 이만큼 끊기면 보드 상태를 모르는 것으로 다룬다.
DRIVE_STATE_TTL_SECONDS = 0.5

# `SET_MODE` 를 보내고 ACK 를 기다리는 상한. 20Hz 시리얼 왕복이라 넉넉하다.
ACK_TIMEOUT_SECONDS = 0.5

# QoS-1 중복 봉투에 같은 답을 재생하기 위한 캐시. `CommandRelay._remember` 와 같은
# FIFO 방식이다.
ANSWERED_CACHE_SIZE = 64

# ---- `esp32_motor_bridge_node` 가 붙여 주는 이름들 (protocol_constants) ----
SET_MODE_MESSAGE_NAME = 'SET_MODE'
ACK_ACCEPTED = 'ACCEPTED'
ACK_REJECTED_STATE = 'REJECTED_STATE'

BOARD_MANUAL_ACTIVE = 'MANUAL_ACTIVE'
BOARD_ESTOP_LATCHED = 'ESTOP_LATCHED'
BOARD_FAULT_LATCHED = 'FAULT_LATCHED'

# 임무가 이 상태면 프레임을 아예 보내지 않는다. 사람이 물리적으로 확인하고 풀어야
# 하는 상태이고(26.5), 보드에 물어봐야 어차피 거부한다.
BLOCKED_MISSION_STATES = {
    MissionState.ESTOP.value: REASON_ESTOP_ACTIVE,
    MissionState.ERROR.value: REASON_ERROR_LATCHED,
}


@dataclass(frozen=True)
class ModeOutcome:
    """한 번의 입력이 만든 결과. 호출부(노드)가 그대로 집행한다.

    넷이 독립이다 — 프레임을 보내면서(`set_mode`) 아직 ack 가 없을 수 있고,
    ack 만 있고 상태 전이가 없을 수도 있으며(거부), 명령 없이 신호만 나올 수도
    있다(모바일 암시적 승격).
    """

    # 보드에 `SET_MODE` 를 보내라. 'MANUAL' 또는 'AUTO'.
    set_mode: str | None = None
    # 상태기계에 넣을 사실.
    signal: Signal | None = None
    # 이 결과가 속한 관제 명령.
    command_id: str | None = None
    # `/mission/command_result` 로 낼 본문(`command-ack.schema.json`). 최종 답이다.
    ack: dict | None = None
    # 사람이 읽을 로그. 아무 일도 없었으면 빈 문자열이다.
    note: str = ''
    # `DRIVE_STATE` 가 끊겼다. 이탈로 **추론하지 않고** 로그만 남긴다.
    stale: bool = False

    @property
    def empty(self) -> bool:
        return (
            self.set_mode is None
            and self.signal is None
            and self.ack is None
            and not self.note
            and not self.stale
        )


@dataclass
class _Pending:
    mode: str
    command_id: str | None
    deadline_s: float


def _ack_body(command_id: str, *, reason_code: str | None, message: str) -> dict:
    """`command-ack.schema.json` 본문. `reason_code` 가 있으면 거부다."""
    return {
        'commandId': command_id,
        'status': ACK_REJECTED if reason_code else ACK_EXECUTED,
        'reasonCode': reason_code,
        'message': message or None,
    }


class ModeGateway:
    """모드 전환 요청 하나를 보드 확인이 끝날 때까지 들고 있는다."""

    def __init__(
        self,
        *,
        manual_confirm_seconds: float = MANUAL_CONFIRM_SECONDS,
        drive_state_ttl_seconds: float = DRIVE_STATE_TTL_SECONDS,
        ack_timeout_seconds: float = ACK_TIMEOUT_SECONDS,
        answered_cache_size: int = ANSWERED_CACHE_SIZE,
    ) -> None:
        self.manual_confirm_seconds = manual_confirm_seconds
        self.drive_state_ttl_seconds = drive_state_ttl_seconds
        self.ack_timeout_seconds = ack_timeout_seconds
        self.answered_cache_size = answered_cache_size

        self._pending: _Pending | None = None
        # 상태기계에 이미 알린 것. **신호를 낼 때만 바뀐다.**
        #
        # 그래서 `MANUAL_ACTIVE → STOPPING → MANUAL_ACTIVE` 가 `MANUAL_ENGAGED` 를
        # 다시 내지 않는다. 초음파 중계의 `STOP_COMMAND` 가 보드를 잠깐 `STOPPING`
        # 으로 보낼 수 있는데, 그때마다 재발행하면 임무 전이가 흔들린다.
        self._reported_manual = False
        self._manual_since_s: float | None = None
        self._drive_state_at_s: float | None = None
        self._stale_logged = False
        self._answered: OrderedDict[str, dict] = OrderedDict()

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    @property
    def pending_mode(self) -> str | None:
        return self._pending.mode if self._pending else None

    # ------------------------------------------------------------------
    # 운영자 의도
    # ------------------------------------------------------------------

    def request(
        self,
        mode: str,
        *,
        command_id: str | None,
        now: float,
        mission_state: str,
        bridge_alive: bool,
    ) -> ModeOutcome:
        """관제 「수동」·「자율」 버튼. 보낼 수 있으면 `set_mode` 를 돌려준다.

        순서가 곧 우선순위다. 캐시 재생이 가장 앞인 이유는 QoS-1 중복이 다른 어떤
        검사보다 먼저 걸러져야 같은 답이 나가기 때문이다 — 두 번째 봉투가 도착할
        무렵에는 `_pending` 이 비어 있거나 임무 상태가 달라져 있을 수 있고, 그러면
        같은 commandId 에 다른 답을 주게 된다.
        """
        if command_id and command_id in self._answered:
            cached = self._answered[command_id]
            return ModeOutcome(
                command_id=command_id,
                ack=dict(cached),
                note=f'중복 명령 {command_id[:8]} 에 같은 답을 재생한다',
            )

        if mode not in REQUESTABLE_MODES:
            return self._reject(
                command_id,
                REASON_INVALID_STATE,
                f'모르는 모드 "{mode}". MANUAL 또는 AUTO 여야 한다',
            )

        blocked = BLOCKED_MISSION_STATES.get(mission_state)
        if blocked:
            # 프레임을 보내지 않는다. 보드에 물어봐도 거부이고, 무엇보다 E-Stop 을
            # 모드 전환으로 우회할 수 있는 것처럼 보이면 안 된다(docs/05 858).
            return self._reject(
                command_id,
                blocked,
                f'{mission_state} latch 상태에서는 모드를 바꾸지 않는다. '
                '운영자가 원인을 확인하고 해제해야 한다',
            )

        if not bridge_alive:
            return self._reject(
                command_id,
                REASON_MOTOR_BOARD_NO_ACK,
                '모터 보드 브리지가 붙어 있지 않다. 로봇 전원과 USB 를 확인한다',
            )

        if self._pending is not None:
            return self._reject(
                command_id,
                REASON_INVALID_STATE,
                f'{self._pending.mode} 전환 응답을 기다리는 중이다. '
                '모드 요청은 한 번에 하나만 처리한다',
            )

        self._pending = _Pending(
            mode=mode,
            command_id=command_id,
            deadline_s=now + self.ack_timeout_seconds,
        )
        return ModeOutcome(
            set_mode=mode,
            command_id=command_id,
            note=f'SET_MODE({mode}) 송출. {self.ack_timeout_seconds:g}초 안에 ACK 를 기다린다',
        )

    # ------------------------------------------------------------------
    # 보드가 알린 사실
    # ------------------------------------------------------------------

    def observe_ack(
        self,
        *,
        acked_message_name: str,
        result_name: str,
        board_state: str,
        now: float,
    ) -> ModeOutcome:
        """`COMMAND_ACK` 하나. `SET_MODE` 에 대한 것만 본다.

        거부 사유는 **새 ack-result 코드 없이 `board_state` 로** 구분한다.
        `REJECTED_STATE` + `MANUAL_ACTIVE` 는 500ms 신선도 가드에 걸렸다는 뜻이며,
        그것이 「자율」 버튼이 거부되는 유일한 정상 사유다.
        """
        if acked_message_name != SET_MODE_MESSAGE_NAME:
            return ModeOutcome()

        pending = self._pending
        if pending is None:
            # 타임아웃 뒤에 늦게 도착했다. 이미 최종 ACK 를 냈으므로 두 번째를 내지
            # 않는다 — 내면 백엔드가 앞의 답을 덮는다.
            return ModeOutcome(
                note=f'기다리지 않는 SET_MODE ACK({result_name}/{board_state})를 버렸다'
            )
        self._pending = None

        if result_name == ACK_ACCEPTED:
            return self._accepted(pending, board_state)

        reason_code = self._classify_rejection(pending.mode, result_name, board_state)
        message = self._rejection_message(reason_code, pending.mode, board_state)
        return self._finish(pending.command_id, reason_code, message)

    def observe_drive_state(self, board_state: str, *, now: float) -> ModeOutcome:
        """50Hz `DRIVE_STATE.state`. 상승 엣지에서만 `MANUAL_ENGAGED` 를 낸다.

        **하강 엣지는 아무것도 내지 않는다.** 모바일 「정지」·deadman 해제·초음파
        중계의 `STOP_COMMAND` 는 전부 바퀴만 0 으로 만들고 수동 권한은 그대로다
        (계획 D11). 보드를 벗어나는 길은 관제 「자율」 하나뿐이고 그것은
        `observe_ack` 로 온다.
        """
        self._drive_state_at_s = now
        self._stale_logged = False

        if board_state != BOARD_MANUAL_ACTIVE:
            # `_reported_manual` 은 건드리지 않는다 — MANUAL_ACTIVE 로 되돌아와도
            # 재발행하지 않기 위해서다.
            self._manual_since_s = None
            return ModeOutcome()

        if self._manual_since_s is None:
            self._manual_since_s = now
        if self._reported_manual:
            return ModeOutcome()

        held = now - self._manual_since_s
        if held < self.manual_confirm_seconds:
            return ModeOutcome()

        self._reported_manual = True
        return ModeOutcome(
            signal=Signal.MANUAL_ENGAGED,
            note=f'보드가 수동을 래치했다({held * 1000:.0f}ms 지속). 임무 상태를 따라간다',
        )

    # ------------------------------------------------------------------
    # 시간 경과
    # ------------------------------------------------------------------

    def tick(self, now: float) -> ModeOutcome:
        """주기 호출. ACK 타임아웃과 `DRIVE_STATE` 스테일을 본다."""
        pending = self._pending
        if pending is not None and now >= pending.deadline_s:
            self._pending = None
            return self._finish(
                pending.command_id,
                REASON_MOTOR_BOARD_NO_ACK,
                f'모터 보드가 {self.ack_timeout_seconds:g}초 안에 '
                f'SET_MODE({pending.mode}) 에 답하지 않았다',
            )

        at = self._drive_state_at_s
        if (
            at is not None
            and not self._stale_logged
            and now - at > self.drive_state_ttl_seconds
        ):
            self._stale_logged = True
            # `_manual_since_s` 만 리셋한다. **이탈로 추론하지 않는다** — "보드가
            # SAFE_IDLE 로 리부트했다" 와 "USB 가 빠졌다" 는 구분할 수 없고, 어느
            # 쪽도 젯슨에 자율 권한을 되돌려 줄 근거가 되지 못한다. 보드 리부트로
            # 젯슨 MANUAL 과 보드 SAFE_IDLE 이 갈라지면 운영자가 「자율」을 눌러
            # 해소한다.
            self._manual_since_s = None
            return ModeOutcome(
                stale=True,
                note=(
                    f'DRIVE_STATE 가 {now - at:.1f}초 끊겼다. 보드 상태를 모르지만 '
                    '수동 이탈로 추론하지 않는다'
                ),
            )

        return ModeOutcome()

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _accepted(self, pending: _Pending, board_state: str) -> ModeOutcome:
        if pending.mode == MODE_MANUAL:
            self._reported_manual = True
            signal = Signal.MANUAL_ENGAGED
        else:
            # 래치가 풀렸다. 다음 상승 엣지는 다시 알려야 한다.
            self._reported_manual = False
            self._manual_since_s = None
            signal = Signal.AUTO_ENGAGED

        message = f'모터 보드가 {pending.mode} 전환을 수락했다(boardState={board_state})'
        ack = None
        if pending.command_id:
            ack = _ack_body(pending.command_id, reason_code=None, message=message)
            self._remember(pending.command_id, ack)
        return ModeOutcome(
            signal=signal,
            command_id=pending.command_id,
            ack=ack,
            note=message,
        )

    @staticmethod
    def _classify_rejection(mode: str, result_name: str, board_state: str) -> str:
        if result_name != ACK_REJECTED_STATE:
            # `REJECTED_STALE_SEQUENCE` 나 모르는 값. 보드가 거부했다는 것 외에는
            # 운영자에게 해석해 줄 것이 없다.
            return REASON_MOTOR_BOARD_REJECTED
        if board_state == BOARD_ESTOP_LATCHED:
            return REASON_ESTOP_ACTIVE
        if board_state == BOARD_FAULT_LATCHED:
            return REASON_ERROR_LATCHED
        if mode == MODE_AUTO and board_state == BOARD_MANUAL_ACTIVE:
            # 500ms 신선도 가드. 유일하게 "다시 시도하면 된다" 인 거부다.
            return REASON_MANUAL_INPUT_ACTIVE
        return REASON_MOTOR_BOARD_REJECTED

    @staticmethod
    def _rejection_message(reason_code: str, mode: str, board_state: str) -> str:
        if reason_code == REASON_MANUAL_INPUT_ACTIVE:
            return (
                '모바일 조종 입력이 계속 들어오는 중이다. 조종을 멈춘 뒤 다시 '
                '시도한다'
            )
        return f'모터 보드가 {mode} 전환을 거부했다(boardState={board_state})'

    def _reject(
        self, command_id: str | None, reason_code: str, message: str
    ) -> ModeOutcome:
        return self._finish(command_id, reason_code, message)

    def _finish(
        self, command_id: str | None, reason_code: str | None, message: str
    ) -> ModeOutcome:
        """최종 답 하나를 만든다. `command_id` 가 없으면 로그만 남는다."""
        if not command_id:
            return ModeOutcome(note=message)
        ack = _ack_body(command_id, reason_code=reason_code, message=message)
        self._remember(command_id, ack)
        return ModeOutcome(command_id=command_id, ack=ack, note=message)

    def _remember(self, command_id: str, ack: dict) -> None:
        self._answered[command_id] = dict(ack)
        self._answered.move_to_end(command_id)
        while len(self._answered) > self.answered_cache_size:
            self._answered.popitem(last=False)

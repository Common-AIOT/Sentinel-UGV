"""모드 전환 게이트웨이 시험 (S15P11A301-298).

시각을 주입하므로 500ms 타임아웃을 500ms 기다리지 않는다. `rclpy` 를 쓰지 않아
ROS 없는 컨테이너에서도 돈다.

여기서 고정하는 것은 셋이다.

1. **commandId 당 최종 ACK 정확히 하나.** 두 번 내면 백엔드가 앞의 답을 덮고
   관제는 `ACCEPTED` 에서 조용히 멈춘다.
2. **거부 사유가 board_state 로 갈린다.** `REJECTED_STATE` + `MANUAL_ACTIVE` 만
   "다시 시도하면 된다" 이고 나머지는 사람이 손을 봐야 한다.
3. **하강 엣지로 수동 이탈을 추론하지 않는다.** 모바일 「정지」·deadman 해제·
   초음파 중계 정지는 전부 바퀴만 0 이고 권한은 그대로다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_mission.mission_state import MissionState, Signal  # noqa: E402
from sentinel_mission.mode_gateway import (  # noqa: E402
    MODE_AUTO,
    MODE_MANUAL,
    ModeGateway,
)

CID = 'c0ffee00-1111-4222-8333-444444444444'
OTHER_CID = 'deadbeef-5555-4666-8777-888888888888'
EXPLORING = MissionState.EXPLORING.value


def gateway() -> ModeGateway:
    return ModeGateway()


def ask(
    gw: ModeGateway,
    mode: str = MODE_MANUAL,
    *,
    command_id: str | None = CID,
    now: float = 0.0,
    mission_state: str = EXPLORING,
    bridge_alive: bool = True,
):
    return gw.request(
        mode,
        command_id=command_id,
        now=now,
        mission_state=mission_state,
        bridge_alive=bridge_alive,
    )


def accept(gw: ModeGateway, *, board_state: str, now: float = 0.1):
    return gw.observe_ack(
        acked_message_name='SET_MODE',
        result_name='ACCEPTED',
        board_state=board_state,
        now=now,
    )


def reject(gw: ModeGateway, *, board_state: str, now: float = 0.1, result='REJECTED_STATE'):
    return gw.observe_ack(
        acked_message_name='SET_MODE',
        result_name=result,
        board_state=board_state,
        now=now,
    )


# ----------------------------------------------------------------------
# 요청 → 프레임
# ----------------------------------------------------------------------


def test_정상_요청은_프레임을_내고_아직_ack를_내지_않는다():
    gw = gateway()

    outcome = ask(gw, MODE_MANUAL)

    assert outcome.set_mode == MODE_MANUAL
    assert outcome.command_id == CID
    assert outcome.ack is None, '최종 답은 보드가 대답한 뒤에만 낸다'
    assert outcome.signal is None
    assert gw.pending_mode == MODE_MANUAL


def test_모르는_모드는_프레임_없이_거부한다():
    gw = gateway()

    outcome = ask(gw, 'RESUME')

    assert outcome.set_mode is None
    assert outcome.ack['status'] == 'REJECTED'
    assert outcome.ack['reasonCode'] == 'INVALID_STATE'


def test_estop_임무에서는_프레임을_보내지_않는다():
    """E-Stop 을 모드 전환으로 우회할 수 있는 것처럼 보이면 안 된다."""
    gw = gateway()

    outcome = ask(gw, MODE_AUTO, mission_state=MissionState.ESTOP.value)

    assert outcome.set_mode is None
    assert outcome.ack['reasonCode'] == 'ESTOP_ACTIVE'
    assert gw.pending_mode is None


def test_error_임무도_같다():
    gw = gateway()

    outcome = ask(gw, MODE_AUTO, mission_state=MissionState.ERROR.value)

    assert outcome.set_mode is None
    assert outcome.ack['reasonCode'] == 'ERROR_LATCHED'


def test_브리지가_없으면_즉시_no_ack로_거부한다():
    """500ms 를 기다려 봐야 구독자가 0명이면 답이 올 곳이 없다."""
    gw = gateway()

    outcome = ask(gw, MODE_AUTO, bridge_alive=False)

    assert outcome.set_mode is None
    assert outcome.ack['reasonCode'] == 'MOTOR_BOARD_NO_ACK'


def test_응답_대기_중_두_번째_요청은_거부한다():
    """`_send_frame` 이 시퀀스를 숨기므로 상관은 '동시 1건 이하'로만 가능하다."""
    gw = gateway()
    ask(gw, MODE_MANUAL, command_id=CID)

    outcome = ask(gw, MODE_AUTO, command_id=OTHER_CID, now=0.05)

    assert outcome.set_mode is None
    assert outcome.ack['reasonCode'] == 'INVALID_STATE'
    assert gw.pending_mode == MODE_MANUAL, '앞의 요청은 그대로 살아 있어야 한다'


def test_commandId가_없어도_프레임은_나간다():
    """ROS 내부에서 부를 수 있다. 그때는 낼 ack 가 없을 뿐이다."""
    gw = gateway()

    outcome = ask(gw, MODE_MANUAL, command_id=None)

    assert outcome.set_mode == MODE_MANUAL
    assert outcome.ack is None


# ----------------------------------------------------------------------
# ACK
# ----------------------------------------------------------------------


def test_수락된_manual은_engaged_신호와_executed_ack를_함께_낸다():
    gw = gateway()
    ask(gw, MODE_MANUAL)

    outcome = accept(gw, board_state='MANUAL_ACTIVE')

    assert outcome.signal is Signal.MANUAL_ENGAGED
    assert outcome.ack['status'] == 'EXECUTED'
    assert outcome.ack['reasonCode'] is None
    assert outcome.ack['commandId'] == CID
    assert gw.pending_mode is None


def test_수락된_auto는_auto_engaged를_낸다():
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = accept(gw, board_state='AUTO_ACTIVE')

    assert outcome.signal is Signal.AUTO_ENGAGED
    assert outcome.ack['status'] == 'EXECUTED'


def test_주행_중_거부는_manual_input_active다():
    """P4 의 핵심. 「자율」이 거부되는 유일한 정상 사유이며 재시도가 답이다."""
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = reject(gw, board_state='MANUAL_ACTIVE')

    assert outcome.signal is None, '거부는 임무 상태를 바꾸지 않는다'
    assert outcome.ack['status'] == 'REJECTED'
    assert outcome.ack['reasonCode'] == 'MANUAL_INPUT_ACTIVE'
    assert '조종을 멈춘 뒤' in outcome.ack['message']


def test_보드_estop_래치_거부는_기존_코드를_재사용한다():
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = reject(gw, board_state='ESTOP_LATCHED')

    assert outcome.ack['reasonCode'] == 'ESTOP_ACTIVE'


def test_보드_fault_래치_거부는_error_latched다():
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = reject(gw, board_state='FAULT_LATCHED')

    assert outcome.ack['reasonCode'] == 'ERROR_LATCHED'


def test_manual_요청이_manual_active로_거부되면_500ms가_아니라_보드_거부다():
    """500ms 가드는 AUTO 요청에만 있다. MANUAL 요청이 거부되는 것은 다른 이야기다."""
    gw = gateway()
    ask(gw, MODE_MANUAL)

    outcome = reject(gw, board_state='MANUAL_ACTIVE')

    assert outcome.ack['reasonCode'] == 'MOTOR_BOARD_REJECTED'


def test_stale_sequence_거부는_보드_거부로_묶는다():
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = reject(
        gw, board_state='MANUAL_ACTIVE', result='REJECTED_STALE_SEQUENCE'
    )

    assert outcome.ack['reasonCode'] == 'MOTOR_BOARD_REJECTED'


def test_다른_메시지의_ack는_보지_않는다():
    gw = gateway()
    ask(gw, MODE_AUTO)

    outcome = gw.observe_ack(
        acked_message_name='STOP_COMMAND',
        result_name='ACCEPTED',
        board_state='STOPPING',
        now=0.1,
    )

    assert outcome.empty
    assert gw.pending_mode == MODE_AUTO, '기다리던 요청을 끝내면 안 된다'


def test_타임아웃_뒤_늦게_온_ack는_두_번째_답을_내지_않는다():
    """두 번 내면 백엔드가 앞의 REJECTED 를 EXECUTED 로 덮는다."""
    gw = gateway()
    ask(gw, MODE_AUTO, now=0.0)
    timeout = gw.tick(0.5)
    assert timeout.ack['reasonCode'] == 'MOTOR_BOARD_NO_ACK'

    late = accept(gw, board_state='AUTO_ACTIVE', now=0.6)

    assert late.ack is None
    assert late.signal is None
    assert late.note


# ----------------------------------------------------------------------
# 타임아웃
# ----------------------------------------------------------------------


def test_ack가_없으면_500ms에_no_ack로_끝낸다():
    gw = gateway()
    ask(gw, MODE_AUTO, now=10.0)

    assert gw.tick(10.4).empty, '아직 기다리는 중이다'

    outcome = gw.tick(10.5)

    assert outcome.ack['status'] == 'REJECTED'
    assert outcome.ack['reasonCode'] == 'MOTOR_BOARD_NO_ACK'
    assert gw.pending_mode is None


def test_타임아웃은_한_번만_난다():
    gw = gateway()
    ask(gw, MODE_AUTO, now=0.0)
    gw.tick(0.5)

    assert gw.tick(1.0).empty


# ----------------------------------------------------------------------
# DRIVE_STATE 관측
# ----------------------------------------------------------------------


def test_수동_래치가_100ms_지속돼야_engaged를_낸다():
    """50Hz 5프레임. 손상된 단일 프레임에 반응하지 않기 위해서다."""
    gw = gateway()

    assert gw.observe_drive_state('MANUAL_ACTIVE', now=0.0).empty
    assert gw.observe_drive_state('MANUAL_ACTIVE', now=0.09).empty

    outcome = gw.observe_drive_state('MANUAL_ACTIVE', now=0.10)

    assert outcome.signal is Signal.MANUAL_ENGAGED


def test_engaged는_한_번만_낸다():
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.2)

    assert gw.observe_drive_state('MANUAL_ACTIVE', now=0.4).empty


def test_하강_엣지는_아무것도_내지_않는다():
    """모바일 「정지」·deadman 해제·초음파 중계 정지는 모드 이탈이 아니다."""
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.2)

    outcome = gw.observe_drive_state('STOPPING', now=0.3)

    assert outcome.empty


def test_manual에서_stopping을_거쳐_돌아와도_재발행하지_않는다():
    """초음파 중계가 보드를 잠깐 STOPPING 으로 보낼 수 있다."""
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.2)
    gw.observe_drive_state('STOPPING', now=0.3)

    gw.observe_drive_state('MANUAL_ACTIVE', now=0.4)
    outcome = gw.observe_drive_state('MANUAL_ACTIVE', now=0.6)

    assert outcome.empty


def test_auto_수락_뒤에는_다음_수동_승격을_다시_알린다():
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.2)
    ask(gw, MODE_AUTO, now=1.0)
    accept(gw, board_state='AUTO_ACTIVE', now=1.1)

    gw.observe_drive_state('MANUAL_ACTIVE', now=2.0)
    outcome = gw.observe_drive_state('MANUAL_ACTIVE', now=2.2)

    assert outcome.signal is Signal.MANUAL_ENGAGED


def test_짧은_수동_깜빡임은_지속_시계를_다시_시작한다():
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('AUTO_ACTIVE', now=0.05)

    assert gw.observe_drive_state('MANUAL_ACTIVE', now=0.11).empty


def test_drive_state가_끊기면_로그만_남기고_이탈로_추론하지_않는다():
    """'보드 리부트' 와 'USB 뽑힘' 은 구분할 수 없다. 어느 쪽도 자율 허가가 아니다."""
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.2)

    outcome = gw.tick(1.0)

    assert outcome.stale is True
    assert outcome.signal is None, '수동 이탈을 추론하면 안 된다'
    assert outcome.ack is None


def test_스테일_로그는_한_번만_난다():
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.tick(1.0)

    assert gw.tick(2.0).empty


def test_다시_들어오면_스테일_로그가_초기화된다():
    gw = gateway()
    gw.observe_drive_state('MANUAL_ACTIVE', now=0.0)
    gw.tick(1.0)
    gw.observe_drive_state('MANUAL_ACTIVE', now=2.0)

    assert gw.tick(3.0).stale is True


# ----------------------------------------------------------------------
# 멱등성 (QoS-1 중복 봉투)
# ----------------------------------------------------------------------


def test_중복_commandId는_같은_답을_재생하고_프레임을_다시_보내지_않는다():
    """`SET_MODE` 는 멱등이 아니다. 3프레임이면 3ACK·3전이가 된다."""
    gw = gateway()
    ask(gw, MODE_AUTO, now=0.0)
    first = reject(gw, board_state='MANUAL_ACTIVE', now=0.1)

    replay = ask(gw, MODE_AUTO, now=0.2)

    assert replay.set_mode is None
    assert replay.ack == first.ack


def test_수락_뒤_중복도_같은_executed를_재생한다():
    gw = gateway()
    ask(gw, MODE_MANUAL, now=0.0)
    first = accept(gw, board_state='MANUAL_ACTIVE', now=0.1)

    replay = ask(gw, MODE_MANUAL, now=0.2)

    assert replay.set_mode is None
    assert replay.ack == first.ack
    assert replay.signal is None, '재생은 전이를 다시 내지 않는다'


def test_캐시는_상한을_넘지_않는다():
    gw = ModeGateway(answered_cache_size=2)
    for index in range(5):
        command_id = f'{index:08d}-0000-4000-8000-000000000000'
        gw.request(
            'BOGUS',
            command_id=command_id,
            now=float(index),
            mission_state=EXPLORING,
            bridge_alive=True,
        )

    assert len(gw._answered) == 2


def test_캐시_재생이_estop_검사보다_먼저다():
    """두 번째 봉투가 도착할 무렵 임무 상태가 달라져 있을 수 있다.

    같은 commandId 에 다른 답을 주면 백엔드 기록이 앞뒤로 뒤집힌다.
    """
    gw = gateway()
    ask(gw, MODE_MANUAL, now=0.0)
    first = accept(gw, board_state='MANUAL_ACTIVE', now=0.1)

    replay = ask(gw, MODE_MANUAL, now=0.2, mission_state=MissionState.ESTOP.value)

    assert replay.ack == first.ack

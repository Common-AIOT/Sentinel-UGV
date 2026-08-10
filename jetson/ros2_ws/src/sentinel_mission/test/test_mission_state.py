"""임무 상태 머신 시험 (S15P11A301-133, 명세 26장).

시간을 주입하므로 최대 상호작용 5분을 5분 기다리지 않는다.

계약 시험도 함께 둔다. 이 노드가 발행하는 JSON이 `common/schemas`를 만족하는지
확인해야 한다. 스키마를 문서로만 두면 다른 팀원이 맞출 대상이 실제 코드와
어긋나도 아무도 모른다(S15P11A301-128에서 확립한 방식).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_mission.mission_state import (  # noqa: E402
    MOVEMENT,
    MissionState,
    MissionStateMachine,
    Phase,
    Signal,
    format_utc,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_DIR = REPO_ROOT / 'common' / 'schemas'

T0 = datetime(2026, 7, 28, 8, 0, 0, tzinfo=timezone.utc)
EID = 'c81f6d20-5a47-4e93-b2d8-1f70e4a95c33'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def exploring() -> MissionStateMachine:
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MISSION_START, now=T0)
    assert machine.state is MissionState.EXPLORING
    return machine


def confirm(machine: MissionStateMachine, *, seconds: float = 1.0, tracks=(7,)):
    return machine.observe_candidates(
        now=at(seconds),
        track_ids=set(tracks),
        confidence=0.9,
        new_encounter_id=EID,
    )


# ----------------------------------------------------------------------
# 정상 경로 (26.3)
# ----------------------------------------------------------------------


def test_full_normal_path_emits_phases_in_order():
    machine = exploring()
    phases = []

    result = confirm(machine)
    phases.append(result.phase)
    assert machine.state is MissionState.PERSON_APPROACHING

    result = machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    phases.append(result.phase)
    assert machine.state is MissionState.INTERACTING

    result = machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(20))
    phases.append(result.phase)
    assert machine.state is MissionState.POST_RECORDING

    # 3초가 지나기 전에는 넘어가지 않는다.
    assert not machine.tick(at(22)).changed
    assert machine.tick(at(23)).changed
    assert machine.state is MissionState.REPORTING

    machine.handle_signal(Signal.REPORT_COMMITTED, now=at(24))
    assert machine.state is MissionState.EXPLORING
    assert machine.encounter is None, 'encounter를 정리하지 않으면 다음 사람을 못 만든다'

    assert phases == [Phase.CONFIRMED, Phase.APPROACHED, Phase.ENDED]


def test_encounter_id_is_stable_across_the_whole_event():
    machine = exploring()
    confirm(machine)
    first = machine.encounter_id
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    assert machine.encounter_id == first, 'encounterId가 바뀌면 이벤트가 쪼개진다'


# ----------------------------------------------------------------------
# 32-6 그룹 (25.4 중복 제거)
# ----------------------------------------------------------------------


def test_three_people_share_one_encounter():
    machine = exploring()
    confirm(machine, tracks=(21,))
    first = machine.encounter_id

    # 사람이 둘 더 보인다. 새 encounter를 만들면 안 된다.
    result = machine.observe_candidates(
        now=at(3), track_ids={21, 22, 23}, new_encounter_id=OTHER
    )
    assert not result.changed
    assert machine.encounter_id == first
    assert machine.person_count == 3


def test_person_count_does_not_shrink_when_someone_is_occluded():
    """한 명이 잠깐 가려도 personCount를 줄이지 않는다.

    보고서의 "몇 명을 발견했는가"가 흔들리면 관제가 신뢰할 수 없다.
    """
    machine = exploring()
    confirm(machine, tracks=(21, 22, 23))
    assert machine.person_count == 3
    machine.observe_candidates(now=at(4), track_ids={21}, new_encounter_id=OTHER)
    assert machine.person_count == 3


def test_already_tracked_ids_are_ignored_with_a_reason():
    machine = exploring()
    confirm(machine, tracks=(7,))
    result = machine.observe_candidates(
        now=at(3), track_ids={7}, new_encounter_id=OTHER
    )
    assert not result.changed
    assert result.ignored_reason, '무시한 이유를 남기지 않으면 원인을 못 찾는다'


# ----------------------------------------------------------------------
# 상실과 재감지 (32-5)
# ----------------------------------------------------------------------


def test_person_lost_after_grace_period_goes_to_post_recording():
    machine = exploring()
    confirm(machine)

    # 빈 배열이 와도 유예 시간 안에는 종료하지 않는다.
    assert not machine.observe_candidates(
        now=at(2), track_ids=set(), new_encounter_id=''
    ).changed
    assert machine.state is MissionState.PERSON_APPROACHING

    result = machine.observe_candidates(
        now=at(1 + 3.0), track_ids=set(), new_encounter_id=''
    )
    assert result.changed
    assert result.phase is Phase.LOST
    assert machine.state is MissionState.POST_RECORDING


def test_redetection_within_post_recording_returns_to_interaction():
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    assert machine.state is MissionState.POST_RECORDING

    result = machine.observe_candidates(
        now=at(11), track_ids={7}, new_encounter_id=OTHER
    )
    assert result.phase is Phase.REDETECTED
    assert machine.state is MissionState.INTERACTING
    assert machine.encounter_id == EID, '재감지가 새 encounter를 만들면 안 된다'


def test_redetection_after_post_recording_window_is_a_new_encounter():
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(5))
    # 아직 INTERACTING이 아니었으므로 무시된다. 정상 경로로 다시 만든다.
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(6))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(7))
    machine.tick(at(11))
    assert machine.state is MissionState.REPORTING
    machine.handle_signal(Signal.REPORT_COMMITTED, now=at(12))

    result = machine.observe_candidates(
        now=at(20), track_ids={7}, new_encounter_id=OTHER
    )
    assert result.phase is Phase.CONFIRMED
    assert machine.encounter_id == OTHER, '보고가 끝난 뒤의 재감지는 새 이벤트다'


def test_absence_during_post_recording_does_not_end_twice():
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    result = machine.observe_candidates(
        now=at(20), track_ids=set(), new_encounter_id=''
    )
    assert not result.changed
    assert machine.state is MissionState.POST_RECORDING


# ----------------------------------------------------------------------
# 순서를 어긴 신호 (26.1의 존재 이유)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    'signal',
    [Signal.SAFE_POSE_REACHED, Signal.DIALOGUE_ENDED, Signal.REPORT_COMMITTED],
)
def test_signals_before_any_encounter_are_ignored(signal):
    """사람을 보기 전에 온 신호는 무시한다.

    여러 노드가 각자 발행하면 실제로 이 순서가 온다. 그대로 처리하면 CONFIRMED
    없이 이벤트가 진행돼 대화 구간이 빠진 영상이 나온다.
    """
    machine = exploring()
    result = machine.handle_signal(signal, now=at(1))
    assert not result.changed
    assert result.ignored_reason
    assert machine.state is MissionState.EXPLORING


def test_signal_for_a_different_encounter_is_ignored():
    """옛 encounter의 지연된 신호가 새 이벤트를 흔들지 않는다."""
    machine = exploring()
    confirm(machine)
    result = machine.handle_signal(
        Signal.SAFE_POSE_REACHED, now=at(5), encounter_id=OTHER
    )
    assert not result.changed
    assert 'encounterId' in result.ignored_reason
    assert machine.state is MissionState.PERSON_APPROACHING


def test_repeated_command_id_is_handled_once():
    """26.4 명령 멱등성."""
    machine = MissionStateMachine()
    first = machine.handle_signal(
        Signal.MISSION_START, now=T0, command_id='cmd-1'
    )
    assert first.changed
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(1))
    second = machine.handle_signal(
        Signal.MISSION_START, now=at(2), command_id='cmd-1'
    )
    assert not second.changed
    assert 'cmd-1' in second.ignored_reason


def test_no_new_encounter_while_paused_or_idle():
    """26.2가 이동을 허용하지 않는 상태에서는 접근하지 않는다.

    encounter를 만들면 녹화만 돌다 타임아웃으로 끝난다.
    """
    for state in (MissionState.SAFE_IDLE, MissionState.PAUSED):
        machine = MissionStateMachine(start_state=state)
        result = machine.observe_candidates(
            now=T0, track_ids={7}, new_encounter_id=EID
        )
        assert not result.changed, f'{state.value}에서 encounter를 만들었다'
        assert machine.encounter is None


# ----------------------------------------------------------------------
# 안전 (26.5)
# ----------------------------------------------------------------------


def test_estop_latches_from_any_state():
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))

    machine.handle_signal(Signal.ESTOP, now=at(6), detail='물리 버튼')
    assert machine.state is MissionState.ESTOP
    assert not machine.movement_allowed

    # latch. 재개도 후보도 통하지 않는다.
    assert not machine.handle_signal(Signal.RESUME_APPROVED, now=at(7)).changed
    assert not machine.observe_candidates(
        now=at(8), track_ids={7}, new_encounter_id=OTHER
    ).changed
    assert machine.state is MissionState.ESTOP


def test_estop_keeps_encounter_so_recorder_can_finalize():
    """E-Stop이 encounter를 버리지 않는다.

    이미 모은 조각으로 녹화 노드가 이벤트를 마감할 수 있어야 한다(32-5).
    """
    machine = exploring()
    confirm(machine)
    result = machine.handle_signal(Signal.ESTOP, now=at(5))
    assert machine.encounter is not None
    assert result.phase is None, 'E-Stop은 phase를 내지 않는다'


def test_paused_does_not_auto_resume_from_voice_request():
    """30.5: 안전 장애가 있으면 자동 재개하지 않고 PAUSED를 유지한다."""
    machine = exploring()
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(1), detail='운영자')
    assert machine.state is MissionState.PAUSED

    result = machine.handle_signal(Signal.RESUME_REQUESTED, now=at(2))
    assert not result.changed
    assert machine.state is MissionState.PAUSED

    assert machine.handle_signal(Signal.RESUME_APPROVED, now=at(3)).changed
    assert machine.state is MissionState.EXPLORING


def test_sensor_fault_goes_to_paused_not_error():
    """복구 가능한 것을 ERROR로 만들면 운영자가 재개할 방법이 없다."""
    machine = exploring()
    machine.handle_signal(Signal.SENSOR_FAULT, now=at(1), detail='라이다 무응답')
    assert machine.state is MissionState.PAUSED


# ----------------------------------------------------------------------
# 시간 전이 (30.5)
# ----------------------------------------------------------------------


def test_max_interaction_time_closes_the_event():
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))

    assert not machine.tick(at(5 + 299)).changed
    result = machine.tick(at(5 + 300))
    assert result.changed
    assert result.phase is Phase.ENDED
    assert machine.state is MissionState.POST_RECORDING


def test_deadline_hint_points_at_the_next_time_transition():
    machine = exploring()
    confirm(machine)
    assert machine.deadline_hint() is None, '접근 중에는 시간 전이가 없다'
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    assert machine.deadline_hint() == at(5 + 300)
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    assert machine.deadline_hint() == at(13)


# ----------------------------------------------------------------------
# 26.2 이동 허용 표
# ----------------------------------------------------------------------


def test_every_state_declares_movement():
    """상태를 추가하고 MOVEMENT를 빠뜨리면 KeyError가 나야 한다.

    조용히 잘못된 값을 쓰면 정지해야 할 상태에서 모터가 돈다.
    """
    for state in MissionState:
        assert state in MOVEMENT, f'{state.value}의 이동 허용이 정의되지 않았다'


def test_interaction_states_forbid_movement():
    """사람과 대화하는 동안 로봇이 움직이면 안 된다(26.2)."""
    for state in (
        MissionState.INTERACTING,
        MissionState.POST_RECORDING,
        MissionState.REPORTING,
        MissionState.ESTOP,
    ):
        machine = MissionStateMachine(start_state=state)
        assert not machine.movement_allowed, f'{state.value}에서 이동을 허용했다'


def test_person_approaching_is_speed_limited():
    """접근 상한은 데드밴드 위·순항 아래여야 한다 (S15P11A301-368).

    30.3 은 0.10m/s 이하로 적었지만 그 속도는 펌웨어 데드밴드(150mm/s) 아래라
    로봇이 못 움직인다. 「사람 앞에서 천천히」라는 의도는 순항(0.30)보다 낮은
    0.25 로 지켜진다.
    """
    machine = MissionStateMachine(start_state=MissionState.PERSON_APPROACHING)
    assert machine.movement_allowed
    assert machine.speed_limit is not None
    assert machine.speed_limit is not None
    assert machine.speed_limit >= 0.15, "데드밴드(150mm/s) 아래면 실속도가 0 이다"
    assert machine.speed_limit < 0.30, "순항보다 느려야 「접근」이다"


# ----------------------------------------------------------------------
# 계약 (common/schemas)
# ----------------------------------------------------------------------


def _validator(name: str):
    jsonschema = pytest.importorskip(
        'jsonschema', reason='jsonschema가 없으면 계약 검증을 건너뛴다'
    )
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding='utf-8'))
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


def test_published_encounter_satisfies_the_schema():
    """이 노드가 내는 encounter가 계약을 만족하는지.

    녹화 노드가 이것을 받으므로 어긋나면 이벤트가 만들어지지 않는다.
    """
    machine = exploring()
    confirm(machine, tracks=(7, 8))
    encounter = machine.encounter
    assert encounter is not None

    body = {
        'encounterId': encounter.encounter_id,
        'phase': Phase.CONFIRMED.value,
        'detectedAt': format_utc(encounter.detected_at),
        'personCount': encounter.person_count,
        'trackIds': sorted(encounter.track_ids),
        'confidence': encounter.confidence,
        'pose': None,
        'missionId': None,
    }
    errors = list(_validator('encounter.schema.json').iter_errors(body))
    assert not errors, [error.message for error in errors]


def test_published_status_satisfies_the_schema():
    machine = exploring()
    confirm(machine)
    body = {
        'state': machine.state.value,
        'controlMode': machine.control_mode,
        'movementAllowed': machine.movement_allowed,
        'speedLimit': machine.speed_limit,
        'changedAt': format_utc(T0),
        'previousState': MissionState.EXPLORING.value,
        'reason': 'encounter confirmed',
        'encounterId': machine.encounter_id,
        'personCount': machine.person_count,
        'recoveryRequired': False,
    }
    errors = list(_validator('mission-status.schema.json').iter_errors(body))
    assert not errors, [error.message for error in errors]


def test_status_payload_keys_match_the_node():
    """이 시험이 노드의 payload 를 **복제**하고 있어 어긋남을 못 잡았다.

    S15P11A301-278 에서 `controlMode` 를 노드에 추가했는데, 위 시험은 자기가 만든
    본문만 검사하므로 노드가 스키마에 없는 필드를 내보내도 통과한다. 실제로
    스키마에 `controlMode` 가 없는 상태로 노드만 고쳐도 초록이었다
    (`additionalProperties: false` 인데 아무도 안 봤다).

    그래서 노드 소스에서 payload 키를 읽어 스키마와 맞춘다. rclpy 없이 돌려야
    하므로 노드를 import 하지 않고 `_publish_status` 의 본문을 파싱한다.
    """
    import ast

    node_source = (
        Path(__file__).resolve().parents[1]
        / 'sentinel_mission' / 'mission_manager_node.py'
    ).read_text(encoding='utf-8')

    tree = ast.parse(node_source)
    keys: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_publish_status':
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict) and inner.keys:
                    candidate = [
                        k.value for k in inner.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
                    if 'state' in candidate:
                        keys = candidate
                        break
            break

    assert keys, '_publish_status 의 payload 를 찾지 못했다'

    schema = json.loads(
        (SCHEMA_DIR / 'mission-status.schema.json').read_text(encoding='utf-8')
    )
    allowed = set(schema['properties'])
    assert set(keys) <= allowed, (
        f'노드가 스키마에 없는 키를 낸다: {sorted(set(keys) - allowed)}. '
        'additionalProperties 가 false 이므로 계약 위반이다'
    )
    for name in schema.get('required', []):
        assert name in keys, f'스키마 필수 키 {name} 를 노드가 내지 않는다'


def test_state_machine_enums_match_the_schemas():
    """코드의 enum과 스키마의 enum이 갈라지지 않게 한다.

    한쪽만 고치면 다른 팀원이 맞출 대상이 실제 코드와 달라진다.
    """
    status = json.loads(
        (SCHEMA_DIR / 'mission-status.schema.json').read_text(encoding='utf-8')
    )
    assert set(status['properties']['state']['enum']) == {
        state.value for state in MissionState
    }

    signal = json.loads(
        (SCHEMA_DIR / 'mission-signal.schema.json').read_text(encoding='utf-8')
    )
    assert set(signal['properties']['signal']['enum']) == {
        item.value for item in Signal
    }

    encounter = json.loads(
        (SCHEMA_DIR / 'encounter.schema.json').read_text(encoding='utf-8')
    )
    assert set(encounter['properties']['phase']['enum']) == {
        phase.value for phase in Phase
    }


def test_growing_group_reemits_confirmed_so_person_count_reaches_the_report():
    """사람이 늘면 CONFIRMED를 다시 낸다.

    phase를 내지 않으면 녹화 보고서의 personCount가 처음 값에 멈춘다. 3명을
    발견했는데 보고서에 1명으로 남으면 32-6이 기록에서 사라진다.
    """
    machine = exploring()
    first = confirm(machine, tracks=(21,))
    assert first.phase is Phase.CONFIRMED
    assert machine.person_count == 1

    grown = machine.observe_candidates(
        now=at(3), track_ids={21, 22, 23}, new_encounter_id=OTHER
    )
    assert grown.phase is Phase.CONFIRMED, 'personCount 변화가 녹화에 전달되지 않는다'
    assert not grown.changed, '상태는 바뀌지 않는다'
    assert machine.person_count == 3
    assert machine.encounter_id == EID

    # 상호작용 중에 늘어도 같다.
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    more = machine.observe_candidates(
        now=at(6), track_ids={21, 22, 23, 24}, new_encounter_id=OTHER
    )
    assert more.phase is Phase.CONFIRMED
    assert machine.person_count == 4


def test_reporting_does_not_reemit_confirmed():
    """보고 중에는 CONFIRMED를 내지 않는다.

    사후 3초가 끝나 녹화 노드가 마감하는 중이므로 새 CONFIRMED가 이벤트를
    되살린다.
    """
    machine = exploring()
    confirm(machine, tracks=(21,))
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    machine.tick(at(14))
    assert machine.state is MissionState.REPORTING

    result = machine.observe_candidates(
        now=at(15), track_ids={21, 22}, new_encounter_id=OTHER
    )
    assert result.phase is None


def test_approach_failed_still_moves_to_interaction():
    """30.3: 접근이 불가능하면 현재 안전 위치에서 음성을 송출한다.

    사람을 향해 무리하게 직진하지 않으므로 접근 상태에 머물러서는 안 된다.
    머물면 최대 상호작용 타임아웃도 걸리지 않아 이벤트가 끝나지 않는다.
    """
    machine = exploring()
    confirm(machine)
    result = machine.handle_signal(
        Signal.APPROACH_FAILED, now=at(5), detail='costmap에 자유 공간이 없다'
    )
    assert result.changed
    assert result.phase is Phase.APPROACHED
    assert machine.state is MissionState.INTERACTING
    assert not machine.movement_allowed
    # 상호작용 타임아웃이 걸려야 이벤트가 끝난다.
    assert machine.deadline_hint() == at(5 + 300)


def test_absence_during_reporting_does_not_loop_back():
    """보고 중에 사람이 없어도 POST_RECORDING으로 되돌아가면 안 된다.

    S15P11A301-139의 무한 루프다. 실물 검증에서 이렇게 반복됐다.

        POST_RECORDING → REPORTING  (3s captured)
        REPORTING → POST_RECORDING  (person lost)
        POST_RECORDING → REPORTING  (3s captured)
        ...

    탐지 노드는 사람이 없는 동안 빈 배열을 계속 발행한다. 그것이 REPORTING과
    만나면 루프가 생긴다. 사람이 5~10초 서 있었는데 이벤트가 마감되지 않아 MP4가
    46.9초가 됐고 46프레임 중 사람이 보이는 것은 하나뿐이었다.
    """
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
    assert machine.tick(at(14)).changed
    assert machine.state is MissionState.REPORTING

    # 빈 배열이 계속 온다. 상태가 바뀌면 안 된다.
    for index in range(10):
        result = machine.observe_candidates(
            now=at(15 + index), track_ids=set(), new_encounter_id=''
        )
        assert not result.changed, f'{index}번째 빈 배열에서 되돌아갔다'
        assert result.phase is None, 'LOST를 다시 내면 녹화 노드가 혼란스럽다'
        assert machine.state is MissionState.REPORTING


def test_every_terminating_state_ignores_absence():
    """종료 절차 상태가 늘면 이 검사도 함께 늘어야 한다.

    조건문에 상태 이름을 직접 쓰면 새 종료 상태가 추가될 때 빠진다. 그래서
    TERMINATING_STATES 목록으로 두고 여기서 전부 확인한다.
    """
    from sentinel_mission.mission_state import TERMINATING_STATES

    assert MissionState.POST_RECORDING in TERMINATING_STATES
    assert MissionState.REPORTING in TERMINATING_STATES

    for state in TERMINATING_STATES:
        machine = exploring()
        confirm(machine)
        machine.state = state
        assert machine.encounter is not None
        # 유예 시간이 지난 뒤에도 상실로 전이하지 않는다.
        result = machine.observe_candidates(
            now=at(1 + 10.0), track_ids=set(), new_encounter_id=''
        )
        assert not result.changed, f'{state.value}에서 상실로 전이했다'
        assert '상실이 정상' in result.ignored_reason


def test_one_person_flickering_does_not_inflate_person_count():
    """한 사람이 들락날락해도 personCount가 늘면 안 된다.

    S15P11A301-139의 실측이다. 사람의 팔 하나가 화면에 세 번 들어왔다 나갔고
    IoU 추적이 매번 새 trackId를 발급했다. 누적 집합으로 세면 3명이 되고, 관제에
    "3명 발견"으로 보고돼 구조 판단이 틀어진다.

    32-6의 기준은 "**동시에** 발견된 사람들"이다.
    """
    machine = exploring()
    confirm(machine, tracks=(1,))
    assert machine.person_count == 1

    # 같은 자리에 새 id로 다시 나타난다. 한 번에 하나씩만 보인다.
    for index, track in enumerate((2, 3), start=1):
        machine.observe_candidates(
            now=at(2 + index * 2), track_ids={track}, new_encounter_id=OTHER
        )
        assert machine.person_count == 1, (
            f'track {track}에서 {machine.person_count}명으로 불어났다'
        )

    # 누적 track은 보고용으로 남는다. 어떤 track이 관여했는지는 유용하다.
    assert machine.encounter is not None
    assert machine.encounter.track_ids == {1, 2, 3}


def test_person_count_uses_simultaneous_not_cumulative():
    """동시에 셋이 보이면 3명, 하나씩 셋이 보이면 1명이다."""
    simultaneous = exploring()
    confirm(simultaneous, tracks=(1,))
    simultaneous.observe_candidates(
        now=at(3), track_ids={1, 2, 3}, new_encounter_id=OTHER
    )
    assert simultaneous.person_count == 3

    sequential = exploring()
    confirm(sequential, tracks=(1,))
    for index, track in enumerate((2, 3), start=1):
        sequential.observe_candidates(
            now=at(2 + index), track_ids={track}, new_encounter_id=OTHER
        )
    assert sequential.person_count == 1


# ----------------------------------------------------------------------
# 관제 명령 (S15P11A301-143, 26.4·26.5·27.4)
# ----------------------------------------------------------------------


CID = '3f2a91c4-5d6e-4a7b-8c9d-0e1f2a3b4c5d'
CID2 = '7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d'


def test_stop_ends_the_mission_from_any_active_state():
    """STOP은 탐사 중이든 상호작용 중이든 임무를 끝낸다.

    23.4가 "사용자 종료"를 종료 조건으로 명시했다. 조작자가 끝내겠다고 했으면
    끝나야 한다.
    """
    from sentinel_mission.mission_state import Signal as S

    for setup in (exploring, lambda: _interacting()):
        machine = setup()
        result = machine.handle_signal(S.MISSION_COMPLETED, now=at(60))
        assert result.changed
        assert machine.state is MissionState.COMPLETED


def _interacting() -> MissionStateMachine:
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    assert machine.state is MissionState.INTERACTING
    return machine


def test_stop_drops_the_in_flight_encounter_but_keeps_the_mission_id():
    """STOP 시 진행 중 encounter는 버리고 missionId는 남긴다.

    encounter를 버리는 이유: 상호작용이 끝나지 않은 상태로 임무가 끝나므로 그
    발견을 완결된 것으로 보고하면 잘못된 기록이 된다.

    **missionId를 남기는 이유는 S15P11A301-171에서 바뀌었다.** 전에는 지웠는데,
    COMPLETED 상태 메시지 자체가 "어느 임무가 끝났는가"를 실어야 한다 — 지도
    저장이 그 값으로 임무별 디렉터리와 maps 행을 만든다. 지우면 발행되는 상태에
    이미 null이 실려 지도가 `no-mission`에 저장된다(실기기에서 겪었다).

    지웠던 근거("이후 encounter가 종료된 임무에 붙는다")는
    `observe_candidates`가 이미 막는다 — 아래 시험이 그것을 고정한다.
    """
    machine = MissionStateMachine()
    mission = '4bde8ad1-c74b-4d42-bec3-9f71af94b41a'
    machine.handle_signal(Signal.MISSION_START, now=T0, mission_id=mission)
    confirm(machine)
    assert machine.encounter is not None
    assert machine.mission_id is not None

    machine.handle_signal(Signal.MISSION_COMPLETED, now=at(30))
    assert machine.encounter is None
    assert machine.mission_id == mission


def test_stop_is_rejected_in_estop_with_a_reason_code():
    """ESTOP latch는 STOP으로 풀리지 않는다 (26.5).

    비상 정지 해제는 운영자가 물리적으로 확인한 뒤 할 일이다. 여기서 STOP을 받아
    COMPLETED로 가면 "정상 종료된 임무"로 기록된다.

    `reason_code`가 있어야 관제가 왜 거부됐는지 표시할 수 있다.
    """
    from sentinel_mission.mission_state import REASON_ESTOP_ACTIVE

    machine = exploring()
    machine.handle_signal(Signal.ESTOP, now=at(10))
    assert machine.state is MissionState.ESTOP

    result = machine.handle_signal(Signal.MISSION_COMPLETED, now=at(20), command_id=CID)
    assert not result.changed
    assert machine.state is MissionState.ESTOP
    assert result.reason_code == REASON_ESTOP_ACTIVE


def test_duplicate_command_id_is_reported_as_duplicate_not_executed():
    """같은 commandId가 두 번 오면 두 번 실행하지 않는다 (26.4).

    QoS 1이 같은 메시지를 두 번 줄 수 있다. bridge가 중복을 걸러내지만 상태
    머신도 자체 가드를 둔다 — 두 층 중 하나가 뚫려도 상태가 두 번 바뀌면 안 된다.

    `reason_code`로 구분되는 것이 중요하다. bridge가 이 값을 보고 "거부"가 아니라
    "이미 처리함"으로 다뤄야 백엔드가 EXECUTED를 REJECTED로 덮어쓰지 않는다.
    """
    from sentinel_mission.mission_state import REASON_DUPLICATE_COMMAND

    machine = MissionStateMachine()
    first = machine.handle_signal(Signal.MISSION_START, now=T0, command_id=CID)
    assert first.changed

    second = machine.handle_signal(Signal.MISSION_START, now=at(1), command_id=CID)
    assert not second.changed
    assert second.reason_code == REASON_DUPLICATE_COMMAND


def test_pause_then_resume_returns_to_exploring():
    """관제의 PAUSE·RESUME이 26.3의 `PAUSED ↔ EXPLORING`을 돈다."""
    machine = exploring()

    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(10), command_id=CID)
    assert machine.state is MissionState.PAUSED

    result = machine.handle_signal(Signal.RESUME_APPROVED, now=at(20), command_id=CID2)
    assert result.changed
    assert machine.state is MissionState.EXPLORING


def test_pressing_pause_twice_is_not_a_rejection():
    """이미 PAUSED인데 PAUSE가 또 오면 거부로 보지 않는다.

    조작자가 버튼을 두 번 눌렀을 때 거부가 뜨면 무엇이 잘못됐는지 찾게 된다.
    원하는 상태에 이미 있으므로 성공이 맞다. `reason_code`가 없는 것이 그 구분이다.
    """
    machine = exploring()
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(10))
    result = machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(11), command_id=CID)
    assert not result.changed
    assert result.reason_code is None, '거부가 아니라 이미 원하는 상태다'


def test_pressing_start_twice_is_not_a_rejection():
    """이미 EXPLORING인데 START가 또 오면 거부로 보지 않는다.

    서로 다른 commandId로 두 번 오면 멱등 가드가 막지 못한다. 그때 INVALID_STATE로
    거부하면 조작자는 임무가 안 시작된 줄 안다.
    """
    machine = exploring()
    result = machine.handle_signal(Signal.MISSION_START, now=at(5), command_id=CID)
    assert not result.changed
    assert machine.state is MissionState.EXPLORING
    assert result.reason_code is None


def test_resume_outside_paused_is_rejected_with_invalid_state():
    """PAUSED가 아닐 때 RESUME은 거부되고 사유 코드가 붙는다."""
    from sentinel_mission.mission_state import REASON_INVALID_STATE

    machine = exploring()
    result = machine.handle_signal(Signal.RESUME_APPROVED, now=at(10), command_id=CID)
    assert not result.changed
    assert result.reason_code == REASON_INVALID_STATE


def test_ack_status_constants_match_the_contract():
    """ACK status 상수가 `command-ack.schema.json`의 enum과 같아야 한다."""
    from sentinel_mission import mission_state as module

    schema = json.loads(
        (SCHEMA_DIR / 'command-ack.schema.json').read_text(encoding='utf-8')
    )
    allowed = set(schema['properties']['status']['enum'])
    constants = {
        module.ACK_ACCEPTED,
        module.ACK_EXECUTED,
        module.ACK_REJECTED,
        module.ACK_EXPIRED,
        module.ACK_FAILED,
    }
    assert constants == allowed


def test_reason_codes_fit_the_contract_length_limit():
    """`reasonCode`는 64자 이하다. 넘으면 백엔드가 본문을 거부한다."""
    from sentinel_mission import mission_state as module

    schema = json.loads(
        (SCHEMA_DIR / 'command-ack.schema.json').read_text(encoding='utf-8')
    )
    limit = schema['properties']['reasonCode']['maxLength']
    codes = [
        module.REASON_ESTOP_ACTIVE,
        module.REASON_ERROR_LATCHED,
        module.REASON_INVALID_STATE,
        module.REASON_DUPLICATE_COMMAND,
        module.REASON_NOT_IMPLEMENTED,
    ]
    for code in codes:
        assert 0 < len(code) <= limit, code


# ----------------------------------------------------------------------
# REPORT_COMMITTED 선도착 (S15P11A301-160)
# ----------------------------------------------------------------------


def test_early_report_committed_skips_reporting():
    """REPORTING 진입 전에 도착한 REPORT_COMMITTED를 잃으면 안 된다.

    실기기 타임라인 재현이다. recorder가 자기 기준으로 먼저 마감해
    REPORT_COMMITTED를 보냈는데 이 머신은 아직 INTERACTING이었다. 신호는
    일회성이고 recorder는 마감한 encounter를 다시 다루지 않으므로
    (S15P11A301-142), 버리면 REPORTING에서 기다릴 것이 영영 오지 않는다 —
    임무가 영구 고착되고 관제 STOP 외에는 복구 수단이 없다.
    """
    machine = _interacting()

    # recorder가 먼저 마감했다. 이 머신은 아직 INTERACTING이다.
    early = machine.handle_signal(
        Signal.REPORT_COMMITTED, now=at(40), encounter_id=EID
    )
    assert not early.changed, '이르게 도착한 신호가 상태를 바꾸면 안 된다'
    assert machine.state is MissionState.INTERACTING

    # 그 뒤에야 사람 소실을 판정한다. 빈 후보가 유예 시간을 넘겨 이어지면
    # observe_candidates가 직접 POST_RECORDING으로 보낸다.
    machine.observe_candidates(now=at(50), track_ids=set(), confidence=None)
    machine.observe_candidates(now=at(54), track_ids=set(), confidence=None)
    assert machine.state is MissionState.POST_RECORDING

    # 3초 뒤: REPORTING에 들어가 기다리는 대신 바로 탐사로 복귀해야 한다.
    result = machine.tick(at(58))
    assert result.changed
    assert machine.state is MissionState.EXPLORING, (
        'REPORTING에서 기다리면 그 신호는 다시 오지 않는다'
    )
    assert machine.encounter is None


def test_early_report_committed_during_post_recording_skips_reporting():
    """POST_RECORDING 중 선도착해도 같은 경합으로 처리한다."""
    machine = _interacting()
    machine.observe_candidates(now=at(50), track_ids=set(), confidence=None)
    machine.observe_candidates(now=at(54), track_ids=set(), confidence=None)
    assert machine.state is MissionState.POST_RECORDING

    early = machine.handle_signal(
        Signal.REPORT_COMMITTED, now=at(55), encounter_id=EID
    )
    assert not early.changed

    result = machine.tick(at(58))
    assert result.changed
    assert machine.state is MissionState.EXPLORING
    assert machine.encounter is None


def test_normal_order_still_waits_in_reporting():
    """정상 순서(REPORTING 진입 후 신호 도착)는 그대로여야 한다."""
    machine = _interacting()
    machine.observe_candidates(now=at(50), track_ids=set(), confidence=None)
    machine.observe_candidates(now=at(54), track_ids=set(), confidence=None)
    assert machine.state is MissionState.POST_RECORDING
    machine.tick(at(58))
    assert machine.state is MissionState.REPORTING, '선도착이 없으면 기다린다'

    result = machine.handle_signal(
        Signal.REPORT_COMMITTED, now=at(60), encounter_id=EID
    )
    assert result.changed
    assert machine.state is MissionState.EXPLORING


def test_discarded_encounter_does_not_leave_an_early_commit_marker():
    """수동 재개로 버린 encounter의 표시가 다음 이벤트로 새면 안 된다."""
    machine = _interacting()
    machine.handle_signal(
        Signal.REPORT_COMMITTED, now=at(40), encounter_id=EID
    )
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(41))
    machine.handle_signal(Signal.RESUME_APPROVED, now=at(42))
    assert machine.state is MissionState.EXPLORING
    assert machine.encounter is None

    # UUID 재사용은 정상 운영에서는 없지만, 같은 ID를 강제로 재사용하면 표시의
    # 수명주기가 encounter에 묶였는지 외부 동작으로 확인할 수 있다.
    confirm(machine, seconds=50)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(51))
    machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(52))
    result = machine.tick(at(56))

    assert result.changed
    assert machine.state is MissionState.REPORTING, (
        '폐기된 encounter의 선도착 표시가 다음 이벤트로 유출됐다'
    )


def test_early_signal_for_a_different_encounter_is_not_remembered():
    """다른 encounter의 지연된 신호가 새 이벤트의 REPORTING을 건너뛰게 하면
    안 된다. encounterId 불일치는 기존 가드가 걸러낸다."""
    machine = _interacting()
    stale = machine.handle_signal(
        Signal.REPORT_COMMITTED, now=at(40), encounter_id=OTHER
    )
    assert not stale.changed
    assert stale.ignored_reason, '불일치 사유가 남아야 한다'

    machine.observe_candidates(now=at(50), track_ids=set(), confidence=None)
    machine.observe_candidates(now=at(54), track_ids=set(), confidence=None)
    result = machine.tick(at(58))
    assert machine.state is MissionState.REPORTING, (
        '남의 encounter 신호로 REPORTING을 건너뛰면 보고 완료 전에 탐사를 재개한다'
    )
    assert result.changed


def test_completed_status_still_carries_the_mission_id():
    """COMPLETED 상태가 어느 임무였는지 실어야 한다 (S15P11A301-171).

    지도 저장이 `/mission/status`의 COMPLETED를 보고 임무별 디렉터리와 maps 행을
    만든다. 전이 전에 `mission_id`를 지우면 발행되는 상태에 이미 null이 실려
    지도가 `no-mission`에 저장된다 — 실기기에서 겪었다.

    종료된 임무에 새 encounter가 붙는 것은 `observe_candidates`가 막는다
    (EXPLORING이 아니면 새 encounter를 만들지 않는다). 아래에서 함께 고정한다.
    """
    machine = MissionStateMachine()
    machine.handle_signal(
        Signal.MISSION_START, now=T0,
        mission_id='4bde8ad1-c74b-4d42-bec3-9f71af94b41a',
    )
    result = machine.handle_signal(Signal.MISSION_COMPLETED, now=at(60))

    assert result.changed
    assert machine.state is MissionState.COMPLETED
    assert machine.mission_id == '4bde8ad1-c74b-4d42-bec3-9f71af94b41a', (
        'COMPLETED 상태 메시지가 어느 임무였는지 알려야 한다'
    )


def test_completed_does_not_accept_new_encounters():
    """종료 후에는 새 encounter를 만들지 않는다.

    `mission_id`를 유지해도 안전한 근거다. 이것이 깨지면 종료된 임무에 발견이
    붙어 백엔드가 거부하는 encounter가 발행된다.
    """
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MISSION_START, now=T0, mission_id='m1')
    machine.handle_signal(Signal.MISSION_COMPLETED, now=at(10))

    result = machine.observe_candidates(
        now=at(20), track_ids={1}, confidence=0.9, new_encounter_id=EID
    )
    assert not result.changed
    assert machine.encounter is None
    assert machine.state is MissionState.COMPLETED


# ----------------------------------------------------------------------
# 종료된 임무로의 귀속 (S15P11A301-307)
#
# COMPLETED 가 들고 있는 missionId 는 종료 보고용이며 그 상태를 떠나면 만료된다.
# 만료되지 않으면 telemetry 봉투가 끝난 임무로 다시 나간다 — 실측에서 6초짜리
# 임무에 101분치 9,574건이 붙었고 그중 78분은 다음 임무의 실제 주행이었다.
# ----------------------------------------------------------------------


MISSION_A = 'b251f573-d781-46a9-a923-58389756937a'
MISSION_B = '93d6f70b-42af-4bc1-a18c-708776e5b946'


def _completed() -> MissionStateMachine:
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MISSION_START, now=T0, mission_id=MISSION_A)
    machine.handle_signal(Signal.MISSION_COMPLETED, now=at(6))
    assert machine.state is MissionState.COMPLETED
    assert machine.mission_id == MISSION_A, 'COMPLETED 메시지가 실을 값이다'
    return machine


def test_pause_after_stop_does_not_revive_the_ended_mission():
    """종료 후 PAUSE 는 끝난 임무의 귀속을 되살리지 않는다.

    실기기에서 일어난 순서 그대로다: STOP 5초 뒤 새 임무 START 가 거부되자
    조작자가 PAUSE 를 눌렀고, PAUSED 는 `MISSION_ACTIVE_BY_STATE` 에서 활성이라
    옛 missionId 가 telemetry 봉투에 다시 실렸다.
    """
    machine = _completed()

    result = machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(90))

    assert result.changed
    assert machine.state is MissionState.PAUSED
    assert machine.mission_id is None, (
        '끝난 임무의 missionId 로 telemetry 가 다시 나간다'
    )


def test_resume_after_stop_keeps_the_mission_id_expired():
    """PAUSE→RESUME 으로 EXPLORING 에 돌아와도 임무 없는 주행이다.

    여기가 실제 피해가 난 지점이다. EXPLORING 은 활성 상태이므로 만료되지 않았다면
    다음 임무가 시작될 때까지의 모든 궤적이 끝난 임무에 쌓인다.
    """
    machine = _completed()
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(90))

    machine.handle_signal(Signal.RESUME_APPROVED, now=at(165))

    assert machine.state is MissionState.EXPLORING
    assert machine.mission_id is None


def test_estop_after_stop_does_not_revive_the_ended_mission():
    """ESTOP 도 같다. 임무 밖의 비상정지는 어느 임무에도 속하지 않는다.

    `MISSION_ACTIVE_BY_STATE` 가 ESTOP 을 활성으로 둔 근거는 "임무 밖에서 났다면
    missionId 가 애초에 null" 이었다. COMPLETED 가 값을 들고 있는 한 그 전제가
    성립하지 않으므로 여기서 만료시킨다.
    """
    machine = _completed()

    machine.handle_signal(Signal.ESTOP, now=at(90))

    assert machine.state is MissionState.ESTOP
    assert machine.mission_id is None


def test_sensor_fault_after_stop_does_not_revive_the_ended_mission():
    """센서 결함으로 PAUSED 에 가는 경로도 만료 대상이다.

    ESTOP·SENSOR_FAULT 는 26.5 우선순위 때문에 다른 신호보다 먼저 처리되어
    핸들러 표를 타지 않는다. 만료를 전이 결과에 걸어 두지 않으면 이 두 경로에서만
    조용히 빠진다 — `_sensor_fault` 는 S15P11A301-276 에서 실제로 그렇게 빠졌다.
    """
    machine = _completed()

    machine.handle_signal(Signal.SENSOR_FAULT, now=at(90), detail='lidar')

    assert machine.state is MissionState.PAUSED
    assert machine.mission_id is None


def test_new_mission_start_from_completed_takes_the_new_id():
    """만료가 새 임무 시작을 방해하지 않는다.

    MISSION_START 는 자기가 missionId 를 들고 오므로 만료 대상이 아니다. 이것이
    깨지면 -274(종료 후 재시작)가 되돌아가 시연이 막힌다.
    """
    machine = _completed()

    machine.handle_signal(Signal.MISSION_START, now=at(90), mission_id=MISSION_B)

    assert machine.state is MissionState.EXPLORING
    assert machine.mission_id == MISSION_B


def test_start_of_another_mission_while_exploring_is_rejected():
    """진행 중 임무가 있으면 새 임무 START 를 거부한다.

    종전에는 `이미 EXPLORING 상태다`로 무시하면서 `reason_code` 가 없어 관제에는
    **EXECUTED** 로 회신됐다. 백엔드는 그것으로 새 임무의 `started_at` 을 채우는데
    로봇은 옛 missionId 로 계속 발행한다 — 관제와 로봇이 서로 다른 임무를 진행
    중이라고 믿는다. 실측 2026-08-05 12:40:12.
    """
    from sentinel_mission.mission_state import REASON_INVALID_STATE

    machine = MissionStateMachine()
    machine.handle_signal(Signal.MISSION_START, now=T0, mission_id=MISSION_A)

    result = machine.handle_signal(
        Signal.MISSION_START, now=at(10), mission_id=MISSION_B, command_id=CID
    )

    assert not result.changed
    assert result.reason_code == REASON_INVALID_STATE, (
        'reason_code 가 없으면 노드가 EXECUTED 로 회신한다'
    )
    assert machine.mission_id == MISSION_A, '진행 중 임무를 갈아 끼우지 않는다'


def test_repeated_start_of_the_same_mission_is_still_a_success():
    """같은 임무의 재요청(버튼 두 번)은 지금처럼 성공으로 본다.

    원하는 상태에 이미 있으므로 거부하면 조작자가 무엇이 잘못됐는지 찾게 된다.
    거부와 멱등을 가르는 것은 missionId 이지 상태가 아니다.
    """
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MISSION_START, now=T0, mission_id=MISSION_A)

    result = machine.handle_signal(
        Signal.MISSION_START, now=at(10), mission_id=MISSION_A, command_id=CID
    )

    assert not result.changed
    assert result.reason_code is None
    assert machine.mission_id == MISSION_A


# ----------------------------------------------------------------------
# controlMode (S15P11A301-278)
#
# command_mux 가 이 값으로 자율/수동을 고른다. 없으면 mux 가 모든 명령을 0으로
# 막는다("모르면 기본값을 자율로 두지 않는다"). 종전에는 mission_manager 가
# 이 필드를 내보내지 않아 안전 체인을 켜도 로봇이 움직이지 않았다.
# ----------------------------------------------------------------------


def test_manual_상태에서만_manual이다():
    machine = MissionStateMachine()
    machine.state = MissionState.MANUAL

    assert machine.control_mode == 'MANUAL'


def test_나머지_상태는_모두_auto다():
    """자율이 기본이 아니라 'MANUAL 이 아니면 자율' 이다.

    수동 전환은 26.3 이 PAUSED 경유로 정했고 MANUAL 이 그 상태이므로,
    '수동인데 임무 진행' 조합이 없다는 것이 이 파생의 전제다.
    """
    for state in MissionState:
        if state is MissionState.MANUAL:
            continue
        machine = MissionStateMachine()
        machine.state = state
        assert machine.control_mode == 'AUTO', f'{state.value} 가 AUTO 가 아니다'


def test_어휘가_mux가_아는_값이다():
    """state.schema.json 의 controlMode 는 MANUAL·AUTO 둘이다.

    mux 는 그 밖의 값을 '모르는 값' 으로 다뤄 명령을 막는다.
    """
    for state in MissionState:
        machine = MissionStateMachine()
        machine.state = state
        assert machine.control_mode in {'MANUAL', 'AUTO'}


def test_초기_상태에서도_값이_있다():
    """None 이면 mux 가 막는다. 기동 직후부터 값이 있어야 한다."""
    machine = MissionStateMachine()

    assert machine.state is MissionState.SAFE_IDLE
    assert machine.control_mode == 'AUTO'


# 일시정지·종료가 encounter 를 끊는다 (S15P11A301-276)
#
# 종전에는 마감 신호(ENDED)를 내지 않아 녹화기가 5분 MAX_DURATION 까지 돌았다.
# 실측: REPORTING 중 관제 PAUSE → 92MB, endReason=MAX_DURATION. 사람이 없는
# 구간까지 담겨 증빙 품질도 낮다.
# ----------------------------------------------------------------------


@pytest.mark.parametrize('state_setup', ['approaching', 'interacting', 'reporting'])
def test_일시정지가_진행_중_encounter를_ended로_끊는다(state_setup):
    machine = exploring()
    confirm(machine)
    if state_setup in ('interacting', 'reporting'):
        machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    if state_setup == 'reporting':
        machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(10))
        machine.tick(at(14))
        assert machine.state is MissionState.REPORTING

    result = machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(20), detail='관제')

    assert result.changed
    assert machine.state is MissionState.PAUSED
    assert result.phase is Phase.ENDED, '마감 신호가 없으면 녹화가 5분까지 간다'


def test_종료가_진행_중_encounter를_ended로_끊는다():
    machine = exploring()
    confirm(machine)

    result = machine.handle_signal(
        Signal.MISSION_COMPLETED, now=at(10), detail='관제 STOP'
    )

    assert result.phase is Phase.ENDED
    assert machine.state is MissionState.COMPLETED


def test_종료는_발행에_필요한_encounter를_전이에_실어_보낸다():
    """순서가 중요하다 — 종료는 encounter 를 버리는데, 전이가 들고 가지 않으면
    노드가 발행 시점에 `encounterId` 를 못 찾아 녹화기가 무시한다."""
    machine = exploring()
    confirm(machine)
    expected = machine.encounter_id

    result = machine.handle_signal(
        Signal.MISSION_COMPLETED, now=at(10), detail='관제 STOP'
    )

    assert machine.encounter is None, '종료 후에는 버려야 다음 임무로 새지 않는다'
    assert result.encounter is not None, '전이가 대상을 들고 가지 않았다'
    assert result.encounter.encounter_id == expected


def test_일시정지는_encounter를_남긴다():
    """재개(_resume_approved)가 버린다. 여기서 버리면 전이가 들고 갈 대상이 없다."""
    machine = exploring()
    confirm(machine)

    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(10), detail='관제')

    assert machine.encounter is not None


def test_encounter가_없으면_phase를_내지_않는다():
    """탐사 중 일시정지는 마감할 것이 없다. 빈 신호를 내면 녹화기가
    「진행 중 이벤트가 아니다」로 무시하지만, 애초에 내지 않는 것이 맞다."""
    machine = exploring()

    result = machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(5), detail='관제')

    assert result.changed
    assert machine.state is MissionState.PAUSED
    assert result.phase is None
    assert result.encounter is None


def test_대기에서_종료해도_phase를_내지_않는다():
    machine = MissionStateMachine()

    result = machine.handle_signal(
        Signal.MISSION_COMPLETED, now=T0, detail='관제 STOP'
    )

    assert machine.state is MissionState.COMPLETED
    assert result.phase is None


def test_phase가_없는_전이는_encounter를_싣지_않는다():
    """전이마다 encounter 를 달면 뜻이 흐려진다 — phase 가 있을 때만이다.

    ESTOP 으로 확인한다. 비상 정지는 encounter 를 끊지 않는 **의도된 예외**이므로
    (원인 조사용으로 전후 영상이 길게 남아야 한다) encounter 가 살아 있는데
    phase 는 없는 유일한 경로다. 이 조합이 없으면 위 가드를 검증할 수 없다.
    """
    machine = exploring()
    confirm(machine)

    result = machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(5))
    assert result.phase is Phase.APPROACHED
    assert result.encounter is not None, 'phase 가 있으면 대상을 실어야 한다'

    result = machine.handle_signal(Signal.ESTOP, now=at(6), detail='물리 버튼')
    assert machine.state is MissionState.ESTOP
    assert machine.encounter is not None, 'ESTOP 은 encounter 를 끊지 않는다'
    assert result.phase is None
    assert result.encounter is None, 'phase 가 없으면 싣지 않는다'


def test_센서_실패도_encounter를_끊는다():
    """PAUSED 로 가는 핸들러가 둘이다 — 관제 PAUSE 와 센서 실패.

    처음에 관제 PAUSE 만 고치고 이 경로를 빠뜨렸다. 센서 실패로 멈추는 것도 그
    발견을 이어갈 수 없는 상황이며, 마감 신호가 없으면 녹화가 5분 상한까지 돈다.
    """
    machine = exploring()
    confirm(machine)

    result = machine.handle_signal(
        Signal.SENSOR_FAULT, now=at(10), detail='lidar 침묵'
    )

    assert machine.state is MissionState.PAUSED
    assert result.phase is Phase.ENDED
    assert result.encounter is not None


def test_센서_실패도_encounter가_없으면_phase를_안_낸다():
    machine = exploring()

    result = machine.handle_signal(Signal.SENSOR_FAULT, now=at(5), detail='x')

    assert machine.state is MissionState.PAUSED
    assert result.phase is None


# ----------------------------------------------------------------------
# 모드 전환 (S15P11A301-298)
#
# 젯슨은 모드를 **판단하지 않고 따라간다**. 액추에이션 중재자는 모터 ESP32 이고,
# 여기 오는 `*_ENGAGED` 는 보드가 이미 그렇게 됐다는 사실이다.
# ----------------------------------------------------------------------


def test_탐사_중_수동_진입은_paused를_경유해_두_전이를_낸다():
    """26.3·14.2 가 정한 2단 전이. 한 전이로 합치면 관제가 PAUSED 를 못 본다."""
    machine = exploring()

    first = machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(5))

    assert first.changed
    assert first.state is MissionState.PAUSED
    assert first.previous is MissionState.EXPLORING
    assert machine.pending_step, '2단이 남아 있어야 한다'

    second = machine.tick(at(5))

    assert second.changed
    assert second.state is MissionState.MANUAL
    assert second.previous is MissionState.PAUSED
    assert not machine.pending_step
    assert machine.control_mode == 'MANUAL'
    assert machine.movement_allowed is False


def test_대기_상태에서는_경유_없이_한_전이로_수동에_간다():
    machine = MissionStateMachine()

    result = machine.handle_signal(Signal.MANUAL_ENGAGED, now=T0)

    assert result.state is MissionState.MANUAL
    assert result.previous is MissionState.SAFE_IDLE
    assert not machine.pending_step, 'SAFE_IDLE 은 경유할 것이 없다'


def test_일시정지에서도_경유_없이_수동에_간다():
    machine = exploring()
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(2))

    result = machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(3))

    assert result.state is MissionState.MANUAL
    assert result.previous is MissionState.PAUSED
    assert not machine.pending_step


def test_수동_진입이_진행_중_encounter를_ended로_끊는다():
    """끊지 않으면 녹화가 5분 상한까지 돈다(S15P11A301-276 과 같은 이유)."""
    machine = exploring()
    confirm(machine)

    first = machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(10))

    assert first.phase is Phase.ENDED
    assert first.encounter is not None, '발행에 필요한 encounter 를 실어야 한다'

    machine.tick(at(10))

    assert machine.encounter is None, '수동 중에 스테일 encounter 를 끌고 가면 안 된다'
    assert machine.encounter_id is None


def test_paused에서_수동으로_들어갈_때도_스테일_encounter를_버린다():
    """PAUSED 는 encounter 를 끊되 버리지 않는다.

    남겨 두면 `_merge_into_encounter` 가 수동 조종 중에도 CONFIRMED 를 계속 낸다.
    `control_mode` docstring 이 이미 닫혔다고 주장하던 구멍이다.
    """
    machine = exploring()
    confirm(machine)
    machine.handle_signal(Signal.PAUSE_REQUESTED, now=at(10))
    assert machine.encounter is not None

    machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(11))

    assert machine.encounter is None


def test_이미_수동이면_거부가_아니다():
    """원하는 상태에 이미 있다. 조작자에게는 성공이므로 reason_code 가 없다."""
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MANUAL_ENGAGED, now=T0)

    result = machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(1))

    assert not result.changed
    assert result.reason_code is None
    assert result.ignored_reason


def test_자율_복귀는_paused에_착지하고_절대_탐사로_가지_않는다():
    """SR-008·30.5. 사람이 로봇 옆에 선 채로 탐사가 재개되면 안 된다."""
    machine = exploring()
    machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(5))
    machine.tick(at(5))
    assert machine.state is MissionState.MANUAL

    result = machine.handle_signal(Signal.AUTO_ENGAGED, now=at(30))

    assert result.changed
    assert result.state is MissionState.PAUSED
    assert result.previous is MissionState.MANUAL
    assert machine.control_mode == 'AUTO'


def test_수동이_아닐_때의_자율_복귀는_거부가_아니다():
    machine = exploring()

    result = machine.handle_signal(Signal.AUTO_ENGAGED, now=at(5))

    assert not result.changed
    assert result.reason_code is None
    assert machine.state is MissionState.EXPLORING


def test_자율_복귀가_남은_수동_2단을_취소한다():
    """1단만 끝난 사이에 「자율」이 오면 다음 tick 이 MANUAL 로 되돌리면 안 된다."""
    machine = exploring()
    machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(5))
    assert machine.pending_step

    machine.handle_signal(Signal.AUTO_ENGAGED, now=at(6))

    assert not machine.pending_step
    assert machine.state is MissionState.PAUSED
    assert not machine.tick(at(7)).changed


def test_전이_중_estop이_끼면_남은_수동_단을_버린다():
    """ESTOP 은 사람이 풀어야 하는 상태다. 수동으로 끌고 가지 않는다(26.5)."""
    machine = exploring()
    machine.handle_signal(Signal.MANUAL_ENGAGED, now=at(5))
    machine.handle_signal(Signal.ESTOP, now=at(5.1), detail='물리 버튼')
    assert machine.state is MissionState.ESTOP

    result = machine.tick(at(5.2))

    assert not result.changed
    assert machine.state is MissionState.ESTOP
    assert not machine.pending_step
    assert '버렸다' in result.ignored_reason


def test_요청_신호가_상태기계에_닿으면_시끄럽게_거부한다():
    """`mode_gateway` 가 가로채야 하는 둘이다. 조용히 무시하면 못 찾는다."""
    for signal in (Signal.MANUAL_REQUESTED, Signal.AUTO_REQUESTED):
        machine = exploring()

        result = machine.handle_signal(signal, now=at(5))

        assert not result.changed
        assert result.reason_code == 'INVALID_STATE'
        assert 'mode_gateway' in result.ignored_reason


def test_manual은_더_이상_미구현이_아니다():
    """`UNIMPLEMENTED` 에 있으면 노드가 전이마다 경고를 낸다."""
    from sentinel_mission.mission_state import UNIMPLEMENTED

    assert MissionState.MANUAL not in UNIMPLEMENTED
    assert UNIMPLEMENTED == frozenset({MissionState.RETURNING})


def test_수동_중에는_새_encounter를_만들지_않는다():
    """`observe_candidates` 가 EXPLORING 만 받는다. 그 불변식을 고정한다."""
    machine = MissionStateMachine()
    machine.handle_signal(Signal.MANUAL_ENGAGED, now=T0)

    result = machine.observe_candidates(
        now=at(5), track_ids={7}, confidence=0.9, new_encounter_id=EID
    )

    assert not result.changed
    assert machine.encounter is None
    assert machine.state is MissionState.MANUAL


def test_redetection_does_not_reset_the_interaction_budget():
    """재감지가 300초 상한을 초기화하지 않는다 (S15P11A301-332).

    되돌리면 사람이 화면에 남아 있는 동안 사후 3초마다 경과 시간이 0이 되어
    `max_interaction_seconds`가 영원히 도달 불가능해진다. 실측에서 대화가 끝난
    뒤에도 재감지 루프가 이어져 녹화가 105초까지 갔고, 링 버퍼가 8초뿐이라
    결과 영상이 오염됐다.
    """
    machine = MissionStateMachine(max_interaction_seconds=30)
    machine.handle_signal(Signal.MISSION_START, now=at(0))
    machine.observe_candidates(now=at(1), track_ids={7}, new_encounter_id=EID)
    machine.handle_signal(Signal.SAFE_POSE_REACHED, now=at(2))
    assert machine.state is MissionState.INTERACTING

    # 3초마다 놓쳤다 되찾기를 반복한다. 예산이 새로 생기면 상한에 닿지 않는다.
    moment = 2
    for _ in range(12):
        machine.handle_signal(Signal.DIALOGUE_ENDED, now=at(moment + 1))
        machine.observe_candidates(
            now=at(moment + 2), track_ids={7}, new_encounter_id=OTHER
        )
        moment += 3

    # 첫 진입(2초)에서 30초를 넘겼으므로 상한이 이벤트를 닫아야 한다.
    machine.tick(at(moment + 1))
    assert machine.state is not MissionState.INTERACTING, (
        '재감지가 상호작용 예산을 초기화해 상한이 듣지 않는다'
    )

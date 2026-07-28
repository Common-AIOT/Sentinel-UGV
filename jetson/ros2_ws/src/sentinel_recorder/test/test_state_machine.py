"""녹화 상태 머신 시험 (S15P11A301-123).

시간을 주입하므로 5분 타임아웃을 5분 기다리지 않고 확인한다. 실제 시간을 쓰면
이 시험이 30분 넘게 걸려 아무도 돌리지 않게 된다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_recorder.state_machine import (  # noqa: E402
    EndReason,
    Phase,
    RecordingState,
    RecordingStateMachine,
)

T0 = datetime(2026, 7, 28, 4, 30, 0, tzinfo=timezone.utc)
EID = 'c81f6d20-5a47-4e93-b2d8-1f70e4a95c33'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def confirmed(machine: RecordingStateMachine, when: float = 0.0, eid: str = EID):
    return machine.on_encounter(eid, Phase.CONFIRMED, at(when), at(when), 1)


# ----------------------------------------------------------------------
# 기본 흐름 (32-5 상태도)
# ----------------------------------------------------------------------


def test_starts_in_buffering():
    assert RecordingStateMachine().state is RecordingState.BUFFERING


def test_confirmed_starts_recording():
    machine = RecordingStateMachine()
    assert confirmed(machine) == 'BUFFERING->RECORDING'
    assert machine.state is RecordingState.RECORDING
    assert machine.recording


def test_full_normal_path():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(EID, Phase.APPROACHED, at(5), at(5)) == (
        'RECORDING->INTERACTION'
    )
    assert machine.on_encounter(EID, Phase.ENDED, at(40), at(40)) == (
        'INTERACTION->POST_RECORDING'
    )
    # 3초가 지나지 않았으면 아직 마무리하지 않는다.
    assert machine.tick(at(42)) is None
    assert machine.tick(at(43)) == 'POST_RECORDING->FINALIZING'
    assert machine.event.end_reason is EndReason.NORMAL
    assert machine.finish(True) == 'FINALIZING->UPLOAD_PENDING'
    assert machine.state is RecordingState.BUFFERING


def test_finish_failure_goes_to_recording_failed():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    machine.tick(at(13))
    assert machine.finish(False) == 'FINALIZING->RECORDING_FAILED'


# ----------------------------------------------------------------------
# 종료 예외 (32-5)
# ----------------------------------------------------------------------


def test_redetection_within_three_seconds_returns_to_interaction():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    assert machine.on_encounter(EID, Phase.REDETECTED, at(12), at(12)) == (
        'POST_RECORDING->INTERACTION'
    )
    assert machine.state is RecordingState.INTERACTION
    # 되돌아왔으므로 3초 경과로 마무리되지 않는다.
    assert machine.tick(at(13)) is None


def test_redetection_after_three_seconds_is_ignored():
    """3초가 지난 재감지를 받아주면 이벤트가 무한히 늘어난다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    assert machine.on_encounter(EID, Phase.REDETECTED, at(14), at(14)) is None
    assert machine.state is RecordingState.POST_RECORDING


def test_no_response_timeout_after_thirty_seconds():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    assert machine.tick(at(20)) is None
    assert machine.tick(at(32)) == 'INTERACTION->POST_RECORDING'
    assert machine.event.end_reason is EndReason.NO_RESPONSE_TIMEOUT


def test_activity_resets_no_response_timer():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    # 25초에 활동이 있으면 타이머가 다시 시작한다.
    machine.on_encounter(EID, Phase.CONFIRMED, at(25), at(25), 2)
    assert machine.tick(at(40)) is None
    assert machine.tick(at(56)) == 'INTERACTION->POST_RECORDING'


def test_max_duration_closes_event_at_five_minutes():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    # 활동을 계속 주어 NO_RESPONSE_TIMEOUT을 피한다.
    for second in range(10, 300, 20):
        machine.on_encounter(EID, Phase.CONFIRMED, at(second), at(second), 1)
    assert machine.tick(at(299)) is None
    assert machine.tick(at(300)) == 'INTERACTION->FINALIZING'
    assert machine.event.end_reason is EndReason.MAX_DURATION


def test_max_duration_wins_over_post_recording():
    """5분을 넘긴 이벤트는 어떤 상태에 있든 닫는다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(299), at(299))
    assert machine.tick(at(301)) == 'POST_RECORDING->FINALIZING'
    assert machine.event.end_reason is EndReason.MAX_DURATION


def test_person_lost_starts_post_recording():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(EID, Phase.LOST, at(8), at(8)) == (
        'RECORDING->POST_RECORDING'
    )
    assert machine.event.end_reason is EndReason.PERSON_LOST


# ----------------------------------------------------------------------
# 다중 인원과 중복 신호 (32-6)
# ----------------------------------------------------------------------


def test_repeated_confirmed_does_not_split_event():
    """32-6. 동시에 발견된 사람들은 encounter 하나를 공유한다.

    CONFIRMED가 여러 번 와도 이벤트가 쪼개지면 안 된다. VID-05가 "사람 3명일 때
    encounter 1개와 MP4 1개"를 요구한다.
    """
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    first_event = machine.event
    assert machine.on_encounter(EID, Phase.CONFIRMED, at(1), at(1), 2) is None
    assert machine.on_encounter(EID, Phase.CONFIRMED, at(2), at(2), 3) is None
    assert machine.event is first_event
    assert machine.event.person_count == 3


def test_other_encounter_is_ignored_while_recording():
    """두 이벤트가 같은 조각을 나눠 가지면 어느 MP4에 넣을지 정할 수 없다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert confirmed(machine, 5, OTHER) is None
    assert machine.event.encounter_id == EID


def test_signals_for_unknown_encounter_are_ignored():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(OTHER, Phase.ENDED, at(5), at(5)) is None
    assert machine.state is RecordingState.RECORDING


def test_signals_without_event_are_ignored():
    machine = RecordingStateMachine()
    for phase in (Phase.APPROACHED, Phase.ENDED, Phase.REDETECTED, Phase.LOST):
        assert machine.on_encounter(EID, phase, at(1), at(1)) is None
    assert machine.state is RecordingState.BUFFERING
    assert machine.tick(at(600)) is None


# ----------------------------------------------------------------------
# recording 플래그와 마감 예정 시각
# ----------------------------------------------------------------------


def test_recording_flag_covers_collection_states():
    machine = RecordingStateMachine()
    assert not machine.recording
    confirmed(machine, 0)
    assert machine.recording
    machine.on_encounter(EID, Phase.APPROACHED, at(1), at(1))
    assert machine.recording
    machine.on_encounter(EID, Phase.ENDED, at(2), at(2))
    assert machine.recording  # POST_RECORDING도 조각을 모은다
    machine.tick(at(6))
    assert not machine.recording  # FINALIZING부터는 모으지 않는다


def test_deadline_hint_points_at_next_transition():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    # POST_RECORDING이면 3초 뒤가 다음 전이다.
    assert machine.deadline_hint(at(11)) == at(13)


# ----------------------------------------------------------------------
# 조각 파일명
# ----------------------------------------------------------------------


def test_local_filename_differs_from_ring_filename():
    """이벤트 디렉터리 파일명이 링 버퍼 파일명과 달라야 한다.

    splitmuxsink가 `max-files`로 파일명을 순환시킨다. 실측으로 segmentId가
    [0..7]에서 [6,7,0,1,..]로 바뀌는 것을 확인했다. 링 파일명을 그대로 쓰면
    서로 다른 조각이 같은 파일을 가리켜 **MP4가 같은 구간을 반복한다.** 그런데
    길이와 프레임 수는 정확히 맞아서 숫자 검증을 통과한다. 68조각 이벤트가
    67.9초 2036프레임으로 나와 정상처럼 보였다.

    이 시험 하나가 그 결함을 막는다.
    """
    from datetime import datetime as _dt

    from sentinel_recorder.segment_store import Segment

    stamp = _dt(2026, 7, 28, tzinfo=timezone.utc)

    def make(segment_id: int, sequence: int) -> Segment:
        return Segment(
            segment_id=segment_id,
            sequence=sequence,
            started_at=stamp,
            ended_at=stamp,
            duration_ms=1000,
            first_pts=0,
            first_frame_key=True,
            path=f'buffer/seg_{segment_id:06d}.ts',
        )

    # 링 파일명이 재사용된 두 조각. sequence는 다르다.
    first = make(segment_id=3, sequence=100)
    second = make(segment_id=3, sequence=108)

    assert first.filename == second.filename, '링 파일명은 순환하므로 같다'
    assert first.local_filename != second.local_filename, (
        '이벤트 디렉터리 이름이 같으면 조각이 덮어써져 영상이 반복된다'
    )
    assert first.local_filename == 'seg_00000100.ts'
    assert second.local_filename.endswith('.ts')

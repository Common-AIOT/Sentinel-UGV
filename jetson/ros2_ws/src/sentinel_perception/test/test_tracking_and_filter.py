"""추적과 25.2 확정 규칙 시험 (S15P11A301-136).

**torch 없이 돈다.** 가짜 박스를 주입하므로 시스템 파이썬에서도, CI에서도 실행된다.
`detector.py`만 ultralytics를 쓰고 그것은 여기서 import하지 않는다.

시간을 주입하므로 1초 안정성을 1초 기다리지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_perception.candidate_filter import (  # noqa: E402
    Candidate,
    CandidateFilter,
)
from sentinel_perception.detector import Box, Detection  # noqa: E402
from sentinel_perception.tracker import IouTracker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[5]
SCHEMA_DIR = REPO_ROOT / 'common' / 'schemas'


def box(x: float, y: float = 0.0, w: float = 80, h: float = 200) -> Box:
    return Box(x=x, y=y, width=w, height=h)


def seen(x: float, confidence: float = 0.9) -> Detection:
    return Detection(box=box(x), confidence=confidence)


# ----------------------------------------------------------------------
# IoU 추적 (25.4)
# ----------------------------------------------------------------------


def test_same_person_keeps_one_track_id_across_frames():
    tracker = IouTracker()
    ids = []
    for index in range(5):
        # 프레임마다 조금씩 움직인다. 겹침이 충분하므로 같은 track이어야 한다.
        tracks = tracker.update([seen(100 + index * 5)], now=index * 0.2)
        ids.append(tracks[0].track_id)
    assert len(set(ids)) == 1, f'같은 사람인데 id가 바뀌었다: {ids}'


def test_two_people_get_two_track_ids():
    tracker = IouTracker()
    tracks = tracker.update([seen(100), seen(400)], now=0.0)
    assert len({track.track_id for track in tracks}) == 2


def test_far_jump_creates_a_new_track():
    """겹침이 없으면 다른 사람이다."""
    tracker = IouTracker()
    first = tracker.update([seen(100)], now=0.0)[0].track_id
    tracks = tracker.update([seen(900)], now=0.2)
    assert first not in {track.track_id for track in tracks if track.misses == 0}


def test_track_survives_a_missed_frame():
    """한 프레임 놓쳐도 track을 지우지 않는다.

    지우면 다음 프레임에서 새 id가 발급되고 25.2의 1초 안정성이 처음부터 다시
    시작한다. 사람이 앞에 있는데도 확정이 계속 미뤄진다.
    """
    tracker = IouTracker(max_misses=3)
    first = tracker.update([seen(100)], now=0.0)[0].track_id

    tracker.update([], now=0.2)
    assert first in tracker.tracks, '한 프레임 놓쳤다고 지우면 안 된다'
    assert tracker.visible() == [], '놓친 track은 보이는 것이 아니다'

    tracks = tracker.update([seen(105)], now=0.4)
    assert tracks[0].track_id == first
    assert tracks[0].misses == 0


def test_track_is_dropped_after_max_misses():
    tracker = IouTracker(max_misses=2)
    tracker.update([seen(100)], now=0.0)
    for index in range(1, 5):
        tracker.update([], now=index * 0.2)
    assert tracker.tracks == {}


def test_visible_excludes_missed_tracks():
    """놓친 track을 후보로 내면 사람이 없는데 이벤트가 이어진다."""
    tracker = IouTracker(max_misses=3)
    tracker.update([seen(100), seen(400)], now=0.0)
    tracker.update([seen(100)], now=0.2)
    visible = tracker.visible()
    assert len(visible) == 1
    assert len(tracker.tracks) == 2


# ----------------------------------------------------------------------
# 25.2 확정 규칙
# ----------------------------------------------------------------------


def test_single_frame_never_confirms():
    """25.2의 첫 문장: 단일 프레임의 박스는 이벤트로 확정하지 않는다."""
    tracker = IouTracker()
    candidate_filter = CandidateFilter(min_observations=3)
    tracker.update([seen(100)], now=0.0)
    assert candidate_filter.confirm(tracker.visible(), now=0.0) == []


def test_confirms_after_minimum_observations_within_window():
    tracker = IouTracker()
    candidate_filter = CandidateFilter(window_seconds=1.0, min_observations=3)

    for index in range(2):
        tracker.update([seen(100 + index * 3)], now=index * 0.2)
        assert candidate_filter.confirm(tracker.visible(), now=index * 0.2) == []

    tracker.update([seen(106)], now=0.4)
    confirmed = candidate_filter.confirm(tracker.visible(), now=0.4)
    assert len(confirmed) == 1
    assert confirmed[0].observations >= 3


def test_observations_outside_the_window_do_not_count():
    """1초에 3번 본 것과 10초에 3번 본 것은 다르다."""
    tracker = IouTracker()
    candidate_filter = CandidateFilter(window_seconds=1.0, min_observations=3)

    # 3초 간격으로 세 번. 창 안에는 언제나 한 번뿐이다.
    for index in range(3):
        tracker.update([seen(100)], now=index * 3.0)
        assert candidate_filter.confirm(tracker.visible(), now=index * 3.0) == []


def test_min_observations_below_two_is_rejected():
    """설정으로도 단일 프레임 확정을 만들 수 없게 한다."""
    with pytest.raises(ValueError, match='2 이상'):
        CandidateFilter(min_observations=1)


def test_sudden_area_change_defers_confirmation():
    """박스가 갑자기 몇 배가 되면 추적이 다른 물체를 이어붙인 것이다 (25.2).

    아래로만 늘린다. 사방으로 키우면 IoU가 문턱 아래로 떨어져 애초에 같은
    track으로 매칭되지 않고, 그러면 급변 검사가 아니라 추적 실패를 시험하게 된다.
    그림자나 바닥 반사를 사람 몸에 붙여 잡을 때 실제로 이 모양이 된다.
    """
    tracker = IouTracker()
    candidate_filter = CandidateFilter(min_observations=2, max_area_ratio=2.0)

    tracker.update([seen(100)], now=0.0)
    candidate_filter.confirm(tracker.visible(), now=0.0)
    tracker.update([seen(102)], now=0.2)
    assert len(candidate_filter.confirm(tracker.visible(), now=0.2)) == 1
    first_id = tracker.visible()[0].track_id

    # 높이만 2.6배. 겹침이 0.38이라 같은 track으로 유지된다.
    tracker.update(
        [Detection(box=box(102, 0, w=80, h=520), confidence=0.9)], now=0.4
    )
    assert tracker.visible()[0].track_id == first_id, '같은 track이어야 한다'
    assert candidate_filter.confirm(tracker.visible(), now=0.4) == []
    assert candidate_filter.last_rejections, '사유를 남기지 않으면 원인을 못 찾는다'


def test_erratic_track_is_not_deleted_only_deferred():
    """급변한 track을 지우면 새 id가 발급돼 안정성이 처음부터 시작한다."""
    tracker = IouTracker()
    candidate_filter = CandidateFilter(min_observations=2, max_area_ratio=2.0)

    tracker.update([seen(100)], now=0.0)
    candidate_filter.confirm(tracker.visible(), now=0.0)
    first_id = tracker.visible()[0].track_id

    # 높이만 2.6배. 겹침이 유지되므로 같은 track이다.
    tracker.update(
        [Detection(box=box(100, 0, w=80, h=520), confidence=0.9)], now=0.2
    )
    assert candidate_filter.confirm(tracker.visible(), now=0.2) == []

    # 안정되면 같은 id로 확정된다.
    tracker.update(
        [Detection(box=box(100, 0, w=80, h=520), confidence=0.9)], now=0.4
    )
    confirmed = candidate_filter.confirm(tracker.visible(), now=0.4)
    assert len(confirmed) == 1
    assert confirmed[0].track_id == first_id


def test_history_is_cleared_when_a_track_disappears():
    """id가 재사용될 때 옛 박스와 비교해 잘못 급변으로 판정하면 안 된다."""
    tracker = IouTracker(max_misses=0)
    candidate_filter = CandidateFilter(min_observations=2)

    tracker.update([Detection(box=box(100, 0, w=200, h=500), confidence=0.9)], now=0.0)
    candidate_filter.confirm(tracker.visible(), now=0.0)
    tracker.update([], now=0.2)
    candidate_filter.confirm(tracker.visible(), now=0.2)

    assert candidate_filter._history == {}


# ----------------------------------------------------------------------
# 계약 (person-candidates.schema.json)
# ----------------------------------------------------------------------


def test_published_body_satisfies_the_schema():
    """이 노드가 내는 JSON이 계약을 만족하는지.

    mission_manager가 이것을 받으므로 어긋나면 사람을 찾아도 이벤트가 만들어지지
    않는다.
    """
    jsonschema = pytest.importorskip(
        'jsonschema', reason='jsonschema가 없으면 계약 검증을 건너뛴다'
    )
    schema = json.loads(
        (SCHEMA_DIR / 'person-candidates.schema.json').read_text(encoding='utf-8')
    )
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )

    tracker = IouTracker()
    candidate_filter = CandidateFilter(min_observations=2)
    for index in range(3):
        tracker.update([seen(100 + index * 4), seen(400 + index * 4)], now=index * 0.2)
    candidates = candidate_filter.confirm(tracker.visible(), now=0.4)
    assert candidates, '확정된 후보가 있어야 계약을 검사할 수 있다'

    body = {
        'observedAt': '2026-07-29T01:23:45.678Z',
        'candidates': [candidate.as_dict() for candidate in candidates],
        'frameId': None,
    }
    errors = list(validator.iter_errors(body))
    assert not errors, [error.message for error in errors]


def test_empty_candidates_body_satisfies_the_schema():
    """사람이 없을 때 보내는 빈 배열도 계약을 만족해야 한다.

    발행을 멈추면 mission_manager가 "사람이 사라진 것"과 "탐지 노드가 죽은 것"을
    구별할 수 없다.
    """
    jsonschema = pytest.importorskip('jsonschema')
    schema = json.loads(
        (SCHEMA_DIR / 'person-candidates.schema.json').read_text(encoding='utf-8')
    )
    validator = jsonschema.Draft202012Validator(schema)
    body = {
        'observedAt': '2026-07-29T01:23:45.678Z',
        'candidates': [],
        'frameId': None,
    }
    assert not list(validator.iter_errors(body))


def test_candidate_box_uses_top_left_origin():
    """계약이 좌상단 기준을 정의한다. 중심 기준으로 내면 관제 표시가 어긋난다."""
    candidate = Candidate(
        track_id=1,
        confidence=0.9,
        box_dict=box(100, 50, w=80, h=200).as_dict(),
        observations=3,
    )
    body = candidate.as_dict()
    assert body['box'] == {'x': 100.0, 'y': 50.0, 'width': 80.0, 'height': 200.0}
    assert body['position'] is None, 'SLAM 전에는 null이다(S15P11A301-137)'


# ----------------------------------------------------------------------
# Box 기하
# ----------------------------------------------------------------------


def test_iou_of_identical_boxes_is_one():
    assert box(0, 0).iou(box(0, 0)) == pytest.approx(1.0)


def test_iou_of_disjoint_boxes_is_zero():
    assert box(0, 0, 10, 10).iou(box(100, 100, 10, 10)) == 0.0


def test_iou_is_symmetric():
    first, second = box(0, 0, 100, 100), box(50, 50, 100, 100)
    assert first.iou(second) == pytest.approx(second.iou(first))

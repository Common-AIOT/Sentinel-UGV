"""자세 판정 규칙과 trackId별 persistence 검증.

실제 영상 없이도 검증 가능한 부분을 결정적으로 확인한다.
합성 keypoint를 쓰므로 모델 정확도가 아니라 **규칙과 배선**을 검증한다.

실행:
    python -m pytest tests -q
    python tests/test_posture_persistence.py     (pytest 없이도 동작)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.persistence import PersistenceTracker  # noqa: E402
from src.pose_estimator import PoseScheduler  # noqa: E402
from src.posture_classifier import PostureClassifier, PostureSmoother  # noqa: E402
from src.motion import MotionTracker  # noqa: E402
from src.schemas import (  # noqa: E402
    POSTURE_NORMAL,
    POSTURE_FALLEN,
    Detection,
    PoseResult,
    PostureResult,
)

KPT_COUNT = 17


def _pose(points: dict[str, tuple[float, float]], conf: float = 0.9) -> PoseResult:
    """이름으로 지정한 keypoint만 채운 PoseResult를 만든다."""
    from src.schemas import KEYPOINT_INDEX

    xy = [(0.0, 0.0)] * KPT_COUNT
    cf = [0.0] * KPT_COUNT
    for name, pt in points.items():
        idx = KEYPOINT_INDEX[name]
        xy[idx] = pt
        cf[idx] = conf
    return PoseResult(keypoints_xy=xy, keypoints_conf=cf)


def _standing_pose() -> PoseResult:
    # 어깨가 위, 엉덩이가 아래 → 상체가 수직
    return _pose(
        {
            "left_shoulder": (100.0, 100.0),
            "right_shoulder": (140.0, 100.0),
            "left_hip": (105.0, 260.0),
            "right_hip": (135.0, 260.0),
            "left_knee": (105.0, 380.0),
            "right_knee": (135.0, 380.0),
        }
    )


def _lying_pose() -> PoseResult:
    # 어깨와 엉덩이의 y가 비슷하고 x만 벌어짐 → 상체가 수평
    return _pose(
        {
            "left_shoulder": (100.0, 200.0),
            "right_shoulder": (100.0, 240.0),
            "left_hip": (260.0, 205.0),
            "right_hip": (260.0, 235.0),
            "left_knee": (380.0, 205.0),
            "right_knee": (380.0, 235.0),
        }
    )


def _det(w: float, h: float, track_id: int | None = 1) -> Detection:
    return Detection(
        class_id=0,
        class_name="person",
        confidence=0.9,
        bbox_xyxy=(0.0, 0.0, w, h),
        track_id=track_id,
    )


def test_standing_is_normal() -> None:
    clf = PostureClassifier()
    # 세로로 긴 bbox (서 있는 사람)
    result = clf.classify(_det(80, 400), _standing_pose())
    assert result.status == POSTURE_NORMAL, result
    assert result.signals["torso_angle_deg"] < 20


def test_lying_is_fallen() -> None:
    clf = PostureClassifier()
    # 가로로 긴 bbox (누운 사람)
    result = clf.classify(_det(400, 120), _lying_pose())
    assert result.status == POSTURE_FALLEN, result
    assert result.fallen_score >= 0.5, result
    # 단일 신호로 판정하지 않는다(AGENTS.md §15)
    assert result.signal_count >= 2, result


def test_missing_pose_still_judges_from_shape() -> None:
    """관절이 없어도 판정을 포기하지 않는다.

    이전 구현은 POSE_UNKNOWN을 내며 이미 계산해둔 bbox 신호까지 버렸다.
    누운 사람은 관절이 가려져도 bbox는 가로로 길다.
    """
    clf = PostureClassifier()
    result = clf.classify(_det(400, 120), None)
    assert result.status == POSTURE_FALLEN, result
    assert result.signal_count == 1, result
    assert "score_bbox_aspect" in result.signals

    # 세로로 긴 bbox는 같은 경로에서 NORMAL이 나와야 한다
    upright = clf.classify(_det(80, 400), None)
    assert upright.status == POSTURE_NORMAL, upright


def test_insufficient_keypoints_falls_back_to_shape() -> None:
    """유효 관절이 부족해도 라벨은 나온다. 근거 부족은 signal_count로 전달한다."""
    clf = PostureClassifier()
    sparse = _pose({"nose": (100.0, 100.0)})
    result = clf.classify(_det(400, 120), sparse)
    assert result.status in (POSTURE_NORMAL, POSTURE_FALLEN)
    assert result.signal_count == 1, result
    assert "부족" in result.signals["torso_reason"], result.signals


def test_smoother_preserves_score_and_signal_count() -> None:
    """완충이 라벨을 뒤집어도 점수와 신호 수는 그대로 실려야 한다.

    완충은 라벨만 바꾼다. 여기서 값을 빠뜨리면 완충이 걸린 관측만 점수 0,
    신호 0으로 보고되어 관제가 근거 없는 판정으로 오인한다.
    """
    sm = PostureSmoother(window=3)
    source = PostureResult(
        status=POSTURE_FALLEN, fallen_score=0.82, signal_count=4, signals={"x": 1}
    )
    # 창을 NORMAL로 채워 다수결이 라벨을 뒤집게 만든다
    sm.smooth(1, PostureResult(status=POSTURE_NORMAL, fallen_score=0.1, signal_count=4))
    sm.smooth(1, PostureResult(status=POSTURE_NORMAL, fallen_score=0.1, signal_count=4))
    out = sm.smooth(1, source)

    assert out.status == POSTURE_NORMAL, "완충이 적용되지 않음(전제 확인)"
    assert out.fallen_score == 0.82, out
    assert out.signal_count == 4, out


def test_inactivity_alone_never_makes_fallen() -> None:
    """부동만으로 FALLEN이 되면 안 된다.

    가만히 서 있는 사람도 부동이다. 부동이 단독으로 임계값을 넘으면 서서 대기하는
    사람이 전부 쓰러진 것으로 보고된다. 부동은 형상이 수평일 때 확신을 올리는
    보조 신호로만 쓴다(문헌의 "최종 몸 방향 + 머문 시간" 조합).
    """
    clf = PostureClassifier()
    # 세로로 긴 bbox(서 있음) + 관절 없음 + 부동 최대
    result = clf.classify(_det(80, 400), None, inactivity=1.0)
    assert result.status == POSTURE_NORMAL, result

    # 관절이 잡히는 경우에도 마찬가지
    with_pose = clf.classify(_det(80, 400), _standing_pose(), inactivity=1.0)
    assert with_pose.status == POSTURE_NORMAL, with_pose

    # 세로가 더 길거나 정사각인 bbox는 부동이 최대여도 뒤집히면 안 된다.
    #
    # 실측(2026-08-03)에서 부동을 독립 항으로 **더했을 때** 문턱이 가로비 0.93까지
    # 내려가, 상반신만 잡힌 앉은 사람(세로가 더 긴 박스)도 FALLEN이 됐다.
    # 배수로 바꾼 뒤 문턱이 1.05로 올라가 이 경우가 막힌다.
    #
    # 가로가 세로보다 긴 박스(>1.0)는 형상 자체가 수평을 가리키므로 여기서
    # 막지 않는다. 그것까지 막으면 누운 사람을 놓친다.
    for w, h in [(80, 100), (95, 100), (100, 100)]:
        near_square = clf.classify(_det(w, h), None, inactivity=1.0)
        assert near_square.status == POSTURE_NORMAL, (
            f"가로비 {w/h:.2f}(세로가 더 김)인데 부동만으로 FALLEN: {near_square}"
        )


def test_inactivity_cannot_flip_taller_than_wide_boxes() -> None:
    """부동이 낮출 수 있는 문턱의 하한을 고정한다.

    부동 배수가 커지면 다시 정사각 이하까지 문턱이 내려간다. 그 회귀를 막는다.
    """
    clf = PostureClassifier()
    lo, hi = 0.3, 4.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if clf.classify(_det(mid * 100, 100), None, inactivity=1.0).status == POSTURE_FALLEN:
            hi = mid
        else:
            lo = mid
    assert hi >= 1.0, f"부동만으로 세로가 더 긴 박스가 FALLEN이 된다 (문턱 가로비 {hi:.3f})"


def test_inactivity_raises_score_when_shape_is_horizontal() -> None:
    """수평 형상에서는 부동이 확신을 올린다."""
    clf = PostureClassifier()
    moving = clf.classify(_det(400, 120), _lying_pose(), inactivity=0.0)
    still = clf.classify(_det(400, 120), _lying_pose(), inactivity=1.0)
    assert still.fallen_score > moving.fallen_score, (moving.fallen_score, still.fallen_score)


def test_motion_tracker_counts_still_time() -> None:
    """움직이면 정지 구간이 리셋되고, 가만히 있으면 점수가 오른다."""
    mt = MotionTracker(still_ratio=0.06, full_still_seconds=3.0)
    mt.update(1, (100.0, 100.0), 100.0, 0.0)
    # 제자리 유지 → 3초 후 만점
    assert mt.update(1, (100.0, 100.0), 100.0, 1.5) == 0.5
    assert mt.update(1, (100.0, 100.0), 100.0, 3.0) == 1.0
    # 크게 움직이면 리셋
    assert mt.update(1, (200.0, 100.0), 100.0, 3.5) == 0.0


def test_motion_tracker_ignores_untracked() -> None:
    """추적 ID가 없으면 이력을 이을 수 없으므로 0을 준다(값을 지어내지 않는다)."""
    mt = MotionTracker()
    assert mt.update(None, (100.0, 100.0), 100.0, 0.0) == 0.0
    assert mt.update(None, (100.0, 100.0), 100.0, 10.0) == 0.0


def test_motion_tracker_normalizes_by_person_size() -> None:
    """이동량을 bbox 크기로 정규화해 거리에 따른 편차를 줄인다.

    멀리 있는 사람은 픽셀 이동량이 작으므로, 절대 픽셀로 재면 항상 정지로 보인다.
    """
    mt = MotionTracker(still_ratio=0.06, full_still_seconds=3.0)
    # 큰 사람(400px)이 10px 이동 → 비율 0.025, 정지로 본다
    mt.update(1, (100.0, 100.0), 400.0, 0.0)
    assert mt.update(1, (110.0, 100.0), 400.0, 3.0) == 1.0
    # 작은 사람(50px)이 같은 10px 이동 → 비율 0.2, 움직인 것으로 본다
    mt.update(2, (100.0, 100.0), 50.0, 0.0)
    assert mt.update(2, (110.0, 100.0), 50.0, 3.0) == 0.0


def test_event_requires_one_second_of_person() -> None:
    """트리거는 '사람을 1초 관측'이다. 자세가 아니다(명세 25.1)."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    fired = []
    for i in range(10):  # 0.0 ~ 0.9초
        fired.append(tracker.update(1, POSTURE_NORMAL, i * 0.1).event_confirmed)
    assert not any(fired), "1초 미만인데 이벤트가 발행됨"

    assert tracker.update(1, POSTURE_NORMAL, 1.0).event_confirmed, "1초 관측했는데 미발행"


def test_standing_person_also_triggers_event() -> None:
    """서 있는 사람도 구조 대상이므로 반드시 이벤트가 나야 한다.

    자세로 거르면 사람을 찾고도 보고하지 않는 false negative가 된다(AGENTS.md §23).
    """
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    for i in range(11):
        state = tracker.update(1, POSTURE_NORMAL, i * 0.1)
    assert state.event_confirmed, "서 있는 사람이 보고되지 않음"
    assert state.fallen_sec == 0.0


def test_normal_posture_still_triggers_event() -> None:
    """자세가 정상이어도 사람은 찾은 것이므로 보고해야 한다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    for i in range(11):
        state = tracker.update(1, POSTURE_NORMAL, i * 0.1)
    assert state.event_confirmed, "정상 자세라고 사람이 누락됨"


def test_fallen_seconds_tracked_as_attribute() -> None:
    """fallen_sec는 트리거가 아니라 속성으로 누적된다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    for i in range(11):
        state = tracker.update(1, POSTURE_FALLEN, i * 0.1)
    assert state.event_confirmed
    assert abs(state.fallen_sec - 1.0) < 1e-6, f"fallen_sec={state.fallen_sec}"
    assert tracker.is_fallen_confirmed(state.fallen_sec)


def test_posture_change_resets_fallen_only() -> None:
    """자세가 바뀌면 fallen_sec만 초기화되고 관측 연속성(seen_sec)은 유지된다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    for i in range(10):
        tracker.update(1, POSTURE_FALLEN, i * 0.1)
    state = tracker.update(1, POSTURE_NORMAL, 1.0)
    assert state.fallen_sec == 0.0, "자세 변경 후에도 fallen_sec가 남음"
    assert state.seen_sec >= 1.0, "자세가 바뀌었다고 관측 시간이 초기화됨"


def test_two_tracks_do_not_share_persistence() -> None:
    """ByteTrack을 도입한 핵심 이유.

    A가 0.6초, B가 0.6초 관측돼도 '1초 연속'이 되어서는 안 된다.
    """
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    fired = []
    for i in range(7):  # track 1: 0.0 ~ 0.6초
        fired.append(tracker.update(1, POSTURE_NORMAL, i * 0.1).event_confirmed)
    for i in range(7, 14):  # track 2: 0.7 ~ 1.3초
        fired.append(tracker.update(2, POSTURE_NORMAL, i * 0.1).event_confirmed)
    assert not any(fired), "서로 다른 사람의 관측 시간이 합산되어 오탐 발생"


def test_untracked_detection_does_not_accumulate() -> None:
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    fired = [tracker.update(None, POSTURE_NORMAL, i * 0.1).event_confirmed for i in range(20)]
    assert not any(fired), "trackId가 없는데 지속시간이 누적됨"


def test_event_cooldown() -> None:
    """연속 관측 중인 사람은 쿨다운 간격으로만 재발행된다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, event_cooldown_seconds=15.0)
    fired_at = []
    # 0.0 ~ 20.0초를 0.1초 간격으로 연속 관측
    for i in range(201):
        t = round(i * 0.1, 3)
        if tracker.update(1, POSTURE_NORMAL, t).event_confirmed:
            fired_at.append(t)
    assert len(fired_at) == 2, f"발행 시각: {fired_at}"
    assert abs(fired_at[0] - 1.0) < 1e-6, f"첫 발행이 1.0초가 아님: {fired_at[0]}"
    assert abs(fired_at[1] - 16.0) < 1e-6, f"두 번째 발행이 16.0초가 아님: {fired_at[1]}"


def test_gap_within_forget_window_continues() -> None:
    """forget_seconds 안에 돌아오면 같은 사람으로 보고 누적을 이어간다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=10.0)
    for i in range(11):
        tracker.update(1, POSTURE_NORMAL, i * 0.1)
    # 5초 공백은 forget(10초) 이내 → 관측 시간 유지
    state = tracker.update(1, POSTURE_NORMAL, 6.0)
    assert state.seen_sec >= 6.0, f"forget 이내인데 관측 시간이 끊김: {state.seen_sec}"


def test_gap_beyond_forget_window_restarts() -> None:
    """forget_seconds를 넘겨 사라졌다 돌아오면 처음부터 다시 센다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=2.0)
    for i in range(11):
        tracker.update(1, POSTURE_NORMAL, i * 0.1)
    # 5초 공백은 forget(2초) 초과 → 리셋
    state = tracker.update(1, POSTURE_NORMAL, 6.0)
    assert state.seen_sec == 0.0, f"forget 초과인데 관측 시간이 이어짐: {state.seen_sec}"


def test_id_switch_inherits_persistence() -> None:
    """가려짐으로 ID가 바뀌어도 관측 시간이 리셋되지 않아야 한다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=10.0)
    pos = (300.0, 200.0)
    for i in range(9):  # track 1로 0.0 ~ 0.8초
        assert not tracker.update(
            1, POSTURE_FALLEN, i * 0.1, center=pos, size=200.0
        ).event_confirmed
    # 0.9초에 가려져 ID가 2로 바뀌었지만 같은 자리
    state = tracker.update(2, POSTURE_FALLEN, 1.0, center=pos, size=200.0)
    assert state.event_confirmed, "ID가 바뀌자 관측 시간이 초기화되어 이벤트를 놓침"


def test_far_away_id_does_not_inherit() -> None:
    """멀리 떨어진 곳에 나타난 새 ID는 승계하면 안 된다. 다른 사람이다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=10.0)
    for i in range(9):
        tracker.update(1, POSTURE_NORMAL, i * 0.1, center=(100.0, 100.0), size=100.0)
    state = tracker.update(2, POSTURE_NORMAL, 1.0, center=(900.0, 700.0), size=100.0)
    assert not state.event_confirmed, "다른 위치의 사람에게 지속 시간이 잘못 승계됨"


def test_stale_track_does_not_inherit() -> None:
    """forget_seconds를 넘겨 끊긴 트랙은 승계 대상이 아니다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=2.0)
    pos = (300.0, 200.0)
    for i in range(9):
        tracker.update(1, POSTURE_NORMAL, i * 0.1, center=pos, size=200.0)
    state = tracker.update(2, POSTURE_NORMAL, 10.0, center=pos, size=200.0)
    assert not state.event_confirmed, "승계 허용 시간을 넘겼는데 승계됨"


def test_inherit_does_not_duplicate() -> None:
    """한 트랙의 상태가 두 개의 새 트랙에 중복 승계되면 안 된다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0, forget_seconds=10.0)
    pos = (300.0, 200.0)
    for i in range(9):
        tracker.update(1, POSTURE_NORMAL, i * 0.1, center=pos, size=200.0)
    first = tracker.update(2, POSTURE_NORMAL, 1.0, center=pos, size=200.0).event_confirmed
    second = tracker.update(3, POSTURE_NORMAL, 1.0, center=pos, size=200.0).event_confirmed
    assert first, "첫 승계가 동작하지 않음"
    assert not second, "같은 상태가 두 트랙에 중복 승계됨"


def test_prune_removes_stale_tracks() -> None:
    tracker = PersistenceTracker(forget_seconds=3.0)
    tracker.update(1, POSTURE_FALLEN, 0.0)
    assert len(tracker._states) == 1
    tracker.prune(10.0)
    assert len(tracker._states) == 0, "오래된 트랙 상태가 정리되지 않음"


def _smoothed(smoother, statuses, track_id=1):
    """상태 시퀀스를 완충기에 통과시켜 결과 시퀀스를 얻는다."""
    out = []
    for s in statuses:
        out.append(smoother.smooth(track_id, PostureResult(status=s)).status)
    return out


def test_smoother_absorbs_single_unknown_blip() -> None:
    """한 프레임 unknown이 튀어도 자세가 흔들리면 안 된다.

    누우면 팔다리가 몸에 가려져 keypoint가 순간 부족해진다. 완충이 없으면
    그때마다 fallen 누적이 끊겨 이벤트를 놓친다.
    """
    sm = PostureSmoother(window=5, )
    seq = [POSTURE_FALLEN] * 3 + [POSTURE_NORMAL] + [POSTURE_FALLEN]
    out = _smoothed(sm, seq)
    assert POSTURE_NORMAL not in out, f"unknown 깜빡임이 그대로 통과됨: {out}"


def test_smoother_reports_unknown_when_all_unknown() -> None:
    """계속 unknown이면 그대로 unknown이어야 한다. 완충이 사실을 감추면 안 된다."""
    sm = PostureSmoother(window=3, )
    out = _smoothed(sm, [POSTURE_NORMAL] * 5)
    assert out[-1] == POSTURE_NORMAL, out


def test_smoother_follows_sustained_change() -> None:
    """자세가 실제로 바뀌면 완충이 지연시키더라도 결국 따라가야 한다."""
    sm = PostureSmoother(window=3, )
    seq = [POSTURE_NORMAL] * 4 + [POSTURE_FALLEN] * 4
    out = _smoothed(sm, seq)
    assert out[-1] == POSTURE_FALLEN, f"지속된 변화를 따라가지 못함: {out}"


def test_smoother_keeps_tracks_separate() -> None:
    """서로 다른 사람의 자세 이력이 섞이면 안 된다."""
    sm = PostureSmoother(window=3, )
    for _ in range(3):
        sm.smooth(1, PostureResult(status=POSTURE_FALLEN))
    out = sm.smooth(2, PostureResult(status=POSTURE_NORMAL)).status
    assert out == POSTURE_NORMAL, f"다른 트랙의 이력이 반영됨: {out}"


def test_smoother_disabled_passes_through() -> None:
    """window=1이면 완충 없이 원본을 그대로 통과시킨다."""
    sm = PostureSmoother(window=1)
    out = _smoothed(sm, [POSTURE_FALLEN, POSTURE_NORMAL, POSTURE_NORMAL])
    assert out == [POSTURE_FALLEN, POSTURE_NORMAL, POSTURE_NORMAL], out


def test_pose_needs_consecutive_frames() -> None:
    """명세 25.6: 사람 3프레임 이상 연속 감지 시에만 Pose를 활성화한다."""
    sch = PoseScheduler(activate_after_frames=3, max_fps=1000.0)
    det = _det(200, 400)
    ran = [sch.should_run(det, i * 0.01) for i in range(5)]
    assert ran == [False, False, True, True, True], ran


def test_pose_rate_limited() -> None:
    """명세 25.6: Pose는 약 2FPS로 제한한다. 매 프레임 돌지 않아야 한다."""
    sch = PoseScheduler(activate_after_frames=1, max_fps=2.0)
    det = _det(200, 400)
    # 0.0 ~ 1.0초를 0.1초 간격으로: 0.0, 0.5, 1.0 세 번만 실행되어야 한다
    ran_at = [round(i * 0.1, 2) for i in range(11) if sch.should_run(det, round(i * 0.1, 2))]
    assert ran_at == [0.0, 0.5, 1.0], ran_at


def test_pose_skips_small_bbox() -> None:
    """작은 bbox는 keypoint를 신뢰할 수 없어 Pose를 돌리지 않는다."""
    sch = PoseScheduler(activate_after_frames=1, min_bbox_width=80, min_bbox_height=80)
    assert not sch.should_run(_det(40, 40), 0.0)
    assert sch.should_run(_det(200, 400), 0.0)


def test_pose_skips_untracked() -> None:
    """추적 ID가 없으면 연속성을 셀 수 없으므로 실행하지 않는다."""
    sch = PoseScheduler(activate_after_frames=3)
    assert not sch.should_run(_det(200, 400, track_id=None), 0.0)


def test_pose_deactivates_after_absence() -> None:
    """명세 25.6: 3초 미감지되면 연속성과 캐시를 초기화한다."""
    sch = PoseScheduler(activate_after_frames=3, max_fps=1000.0, deactivate_after_seconds=3.0)
    det = _det(200, 400)
    for i in range(5):
        sch.should_run(det, i * 0.01)
    sch.cache(1, PostureResult(status=POSTURE_FALLEN))
    assert sch.cached(1) is not None
    # 5초 공백 후 재등장 → 다시 3프레임 연속이 필요하고 캐시도 비어 있어야 한다
    assert not sch.should_run(det, 5.0), "공백 후에도 즉시 실행됨"
    assert sch.cached(1) is None, "공백 후에도 이전 자세 캐시가 남음"


def test_pose_cache_reused_between_runs() -> None:
    """실행하지 않는 프레임에서는 직전 판정을 재사용한다(자세 깜빡임 방지)."""
    sch = PoseScheduler(activate_after_frames=1, max_fps=2.0)
    det = _det(200, 400)
    assert sch.should_run(det, 0.0)
    sch.cache(1, PostureResult(status=POSTURE_FALLEN))
    assert not sch.should_run(det, 0.1), "2FPS 제한인데 즉시 재실행됨"
    assert sch.cached(1).status == POSTURE_FALLEN


def test_pose_global_budget_does_not_scale_with_person_count() -> None:
    """명세 431행의 "약 2FPS"는 파이프라인 전체 예산이다.

    사람이 4명이어도 초당 Pose 실행이 max_fps를 넘으면 안 된다. track별로만
    제한하면 초당 2N회가 되어 Jetson에서 Detect FPS를 잡아먹는다
    (S15P11A301-150 실측: 기대 약 127회 대비 실제 300회).
    """
    sch = PoseScheduler(activate_after_frames=1, max_fps=2.0, global_budget=True)
    people = [_det(200, 400, track_id=i) for i in range(1, 5)]

    runs = 0
    # 1초 구간을 0.05초 간격(20FPS)으로 돈다.
    for step in range(20):
        runs += len(sch.select(people, round(step * 0.05, 2)))

    # 2FPS 예산이므로 1초에 2~3회(경계 포함). 사람 수 4를 곱한 8회가 나오면 안 된다.
    assert runs <= 3, f"전역 예산이 적용되지 않음: 1초에 {runs}회"


def test_pose_per_track_budget_scales_with_person_count() -> None:
    """global_budget=False면 예전 동작(사람 수에 비례)으로 돌아간다.

    A/B 비교용 스위치가 실제로 동작하는지 고정한다. 이 값이 기본이 되면 안 된다.
    """
    sch = PoseScheduler(activate_after_frames=1, max_fps=2.0, global_budget=False)
    people = [_det(200, 400, track_id=i) for i in range(1, 5)]

    runs = 0
    for step in range(20):
        runs += len(sch.select(people, round(step * 0.05, 2)))

    assert runs >= 8, f"track별 예산인데 사람 수에 비례하지 않음: {runs}회"


def test_pose_global_budget_round_robins() -> None:
    """전역 예산은 가장 오래 갱신되지 않은 사람에게 차례를 준다.

    한 명만 계속 갱신되고 나머지가 굶으면, 쓰러진 사람을 영영 못 볼 수 있다.
    """
    sch = PoseScheduler(activate_after_frames=1, max_fps=2.0, global_budget=True)
    people = [_det(200, 400, track_id=i) for i in range(1, 4)]

    served: list[int] = []
    # 0.5초 간격이면 매 호출마다 예산이 정확히 한 번씩 열린다.
    for step in range(6):
        served.extend(sorted(sch.select(people, round(step * 0.5, 2))))

    assert len(served) == 6, served
    # 3명이 두 바퀴씩 돌아야 한다. 특정 ID가 독점하면 실패한다.
    assert sorted(served) == [1, 1, 2, 2, 3, 3], served


def test_pose_select_respects_consecutive_frames() -> None:
    """select()도 명세의 3프레임 연속 조건을 지킨다(should_run과 동일 기준)."""
    sch = PoseScheduler(activate_after_frames=3, max_fps=1000.0, global_budget=True)
    people = [_det(200, 400, track_id=1)]
    ran = [bool(sch.select(people, round(i * 0.01, 2))) for i in range(5)]
    assert ran == [False, False, True, True, True], ran


def _pose_wh(shoulder_y: float, hip: tuple[float, float], half_width: float = 30.0,
             cx: float = 150.0) -> PoseResult:
    """어깨 폭이 명시된 상체 pose. depth_tilt 검증용."""
    from src.schemas import KEYPOINT_INDEX

    xy = [(0.0, 0.0)] * KPT_COUNT
    cf = [0.0] * KPT_COUNT
    for name, pt in [
        ("left_shoulder", (cx - half_width, shoulder_y)),
        ("right_shoulder", (cx + half_width, shoulder_y)),
        ("left_hip", hip),
        ("right_hip", hip),
        ("nose", (cx, shoulder_y - 25)),
        ("left_knee", (hip[0], hip[1] + 40)),
    ]:
        idx = KEYPOINT_INDEX[name]
        xy[idx] = pt
        cf[idx] = 0.9
    return PoseResult(keypoints_xy=xy, keypoints_conf=cf)


def test_depth_tilt_catches_person_lying_along_optical_axis() -> None:
    """카메라 광축 방향으로 누운 사람을 잡는다.

    torso_angle은 이미지 dx/dy 기반이라 앞뒤 기울기를 못 본다. 복도를 주행하는
    UGV에서는 복도 방향으로 누운 사람이 정확히 이 배치가 되므로 사각지대였다.
    """
    det = _det(140, 110, track_id=1)
    # 어깨 폭 60, 상체가 30px로 단축(똑바로 서면 78px) → 앞뒤로 크게 기움
    pose = _pose_wh(180.0, (150.0, 210.0))

    off = PostureClassifier(depth_tilt=False).classify(det, pose)
    on = PostureClassifier(depth_tilt=True).classify(det, pose)

    assert off.signals["torso_angle_deg"] == 0.0, "좌우 각도는 0이어야 한다(전제 확인)"
    assert off.status == POSTURE_NORMAL, "기존 규칙은 이 자세를 놓친다(회귀 기준)"
    assert on.status == POSTURE_FALLEN, f"앞뒤 기울기를 못 잡음: {on.signals}"


def test_depth_tilt_does_not_flag_upright_person() -> None:
    """똑바로 선 사람에게 오탐을 만들지 않는다."""
    det = _det(60, 300, track_id=1)
    # 어깨 폭 60, 상체 78 = 60 * 1.3 (기대비와 일치) → 기울기 0
    pose = _pose_wh(100.0, (150.0, 178.0))
    result = PostureClassifier(depth_tilt=True).classify(det, pose)
    assert result.signals["torso_angle_depth_deg"] == 0.0, result.signals
    assert result.status == POSTURE_NORMAL


def test_depth_tilt_ignores_turned_person_instead_of_guessing() -> None:
    """몸을 옆으로 돌려 어깨 폭이 좁아진 경우 각도를 부풀리지 않는다.

    관측 비가 기대비보다 커지면 cos가 1을 넘는데, 이때 1로 잘라 0도로 둔다.
    이 추정은 과소 추정 방향으로만 틀려야 한다. 반대로 틀리면 서 있는 사람이
    쓰러진 것으로 보고되어 구조 우선순위가 잘못 올라간다.
    """
    det = _det(60, 300, track_id=1)
    # 어깨 폭 10(옆으로 돌아섬)인데 상체는 78 → 관측비 7.8 (기대비 1.3보다 훨씬 큼)
    pose = _pose_wh(100.0, (150.0, 178.0), half_width=5.0)
    result = PostureClassifier(depth_tilt=True).classify(det, pose)
    assert result.signals["torso_angle_depth_deg"] == 0.0, result.signals
    assert result.status == POSTURE_NORMAL


def test_depth_tilt_skipped_when_one_shoulder_missing() -> None:
    """어깨가 한쪽만 잡히면 폭을 알 수 없으므로 이 신호를 쓰지 않는다."""
    from src.schemas import KEYPOINT_INDEX

    xy = [(0.0, 0.0)] * KPT_COUNT
    cf = [0.0] * KPT_COUNT
    for name, pt in [
        ("left_shoulder", (120.0, 180.0)),
        ("left_hip", (150.0, 210.0)),
        ("right_hip", (150.0, 210.0)),
        ("nose", (150.0, 155.0)),
        ("left_knee", (150.0, 250.0)),
    ]:
        idx = KEYPOINT_INDEX[name]
        xy[idx] = pt
        cf[idx] = 0.9
    pose = PoseResult(keypoints_xy=xy, keypoints_conf=cf)

    result = PostureClassifier(depth_tilt=True).classify(_det(140, 110), pose)
    assert "torso_angle_depth_deg" not in result.signals, result.signals


def test_pose_scheduler_exposes_bbox_thresholds() -> None:
    """이벤트 강제 Pose(_fill_pose_for_event)가 같은 크기 기준을 재사용한다.

    파이프라인이 이 속성으로 판단하므로 이름이 바뀌면 조용히 깨진다.
    작은 bbox에 Pose를 돌리면 keypoint를 믿을 수 없는데도 증빙에 실린다.
    """
    sch = PoseScheduler(min_bbox_width=80, min_bbox_height=90)
    assert sch.min_bbox_width == 80
    assert sch.min_bbox_height == 90


def test_no_person_no_event() -> None:
    """사람이 없으면 아무 이벤트도 나면 안 된다."""
    tracker = PersistenceTracker(person_confirm_seconds=1.0)
    assert not tracker.update(None, POSTURE_NORMAL, 0.0).event_confirmed
    tracker.prune(100.0)
    assert len(tracker._states) == 0


# 2026-08-04 실측(관측 27,556건)에서 FALLEN 오탐 집단의 평균값이다.
# 합성값이 아니라 관측된 수치를 그대로 재현한다.
#   torso_angle_deg 10.98 · torso_shoulder_ratio 1.967 · vertical_extent_ratio 0.140
#   bbox_aspect_ratio 1.161 · inactivity 0.979
_SEATED_FP_ASPECT = 1.159
_SEATED_FP_W, _SEATED_FP_H = 640.0, 552.0


def _seated_occluded_pose() -> PoseResult:
    """앉아 있고 하체가 가려져 엉덩이가 어깨 근처로 잘못 찍힌 경우.

    상체는 거의 수직(11°)인데 어깨-엉덩이 y 간격이 몸 높이의 14%로 나온다.
    이 둘은 물리적으로 양립할 수 없으며, 틀린 쪽은 엉덩이 keypoint다.
    실측에서 FALLEN일 때 엉덩이 검출률은 23~28%, NORMAL은 41~45%였다.

    어깨 폭 40, 상체 길이 78.7(비 1.967)이라 앞뒤 기울기 추정은 0°가 된다
    (기대비 1.3보다 길어 clamp). 즉 각도는 순수하게 좌우 기울기 10.98°다.
    """
    return _pose(
        {
            "left_shoulder": (100.0, 100.0),
            "right_shoulder": (140.0, 100.0),
            "left_hip": (120.0, 177.3),
            "right_hip": (150.0, 177.3),
        }
    )


def test_seated_false_positive_reproduces_without_gate() -> None:
    """게이트가 없으면 실측 오탐이 그대로 재현된다. 회귀 기준선이다."""
    clf = PostureClassifier(upright_angle_deg=0.0)
    det = _det(w=_SEATED_FP_W, h=_SEATED_FP_H)
    result = clf.classify(det, _seated_occluded_pose(), inactivity=0.979)
    # 각도는 수직인데 extent가 수평이라고 말한다 — 모순이 점수를 밀어 올린다
    assert result.signals["torso_angle_deg"] < 15.0
    assert result.signals["score_vertical_extent"] > 0.7
    assert result.status == POSTURE_FALLEN, result.reason


def test_upright_gate_rejects_seated_person_with_bad_hips() -> None:
    """상체가 수직이면 수직신장비가 이상해도 FALLEN이 되지 않는다."""
    clf = PostureClassifier()
    det = _det(w=_SEATED_FP_W, h=_SEATED_FP_H)
    # 부동까지 최대로 줘서 가장 불리한 조건을 만든다.
    result = clf.classify(det, _seated_occluded_pose(), inactivity=1.0)
    assert result.signals.get("vertical_extent_dropped") is True
    assert "score_vertical_extent" not in result.signals
    assert result.status == POSTURE_NORMAL, result.reason


def test_upright_gate_keeps_torso_angle_signal() -> None:
    """게이트는 extent만 버리고 각도 신호는 그대로 쓴다."""
    clf = PostureClassifier()
    result = clf.classify(
        _det(w=_SEATED_FP_W, h=_SEATED_FP_H), _seated_occluded_pose(), inactivity=1.0
    )
    assert "score_torso_angle" in result.signals
    # extent가 빠졌으므로 관절 신호 1개 + 형상 1개 + 부동 1개 = 3
    assert result.signal_count == 3


def test_upright_gate_does_not_touch_lying_person() -> None:
    """수평인 사람은 게이트에 걸리지 않는다. 정탐을 해치면 안 된다."""
    clf = PostureClassifier()
    result = clf.classify(_det(w=300.0, h=120.0), _lying_pose(), inactivity=1.0)
    assert result.signals.get("vertical_extent_dropped") is not True
    assert "score_vertical_extent" in result.signals
    assert result.status == POSTURE_FALLEN, result.reason


def test_upright_gate_can_be_disabled() -> None:
    """0으로 두면 이전 동작으로 되돌아간다(롤백 경로)."""
    clf = PostureClassifier(upright_angle_deg=0.0)
    result = clf.classify(_det(w=180.0, h=200.0), _seated_occluded_pose(), inactivity=1.0)
    assert result.signals.get("vertical_extent_dropped") is not True
    assert "score_vertical_extent" in result.signals


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())

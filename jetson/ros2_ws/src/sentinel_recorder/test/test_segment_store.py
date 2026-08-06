"""조각 연속성 판정 시험 (S15P11A301-304).

**이 시험이 고정하는 것은 「무엇을 실패로 볼 것인가」다.** 종전에는 이웃 조각의
`firstPts`가 같기만 해도 역행으로 보고 이벤트 MP4를 통째로 버렸다. 실측에서
젯슨 `pending` 39건 중 17건이 그렇게 영상 없이 끝났다.

동률이 정상인 이유는 `firstPts`가 그 조각의 첫 프레임 값이 아니기 때문이다 —
링 writer가 조각 경계에서 읽는 「마지막으로 밀어 넣은 입력 PTS」이고, 미는 쪽과
읽는 쪽이 다른 스레드라 조각 사이에 새 프레임이 없으면 같은 값이 남는다.

그렇다고 검사를 없애면 안 된다. 진짜 역행(리베이스가 조각 경계 밖에서 일어난 것)을
concat 하면 재생이 깨진다. 그래서 아래 두 가지를 한 쌍으로 고정한다.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_recorder.segment_store import (  # noqa: E402
    Segment,
    continuity_report,
    ordering_pts_source,
)

T0 = datetime(2026, 8, 6, 6, 0, 0, tzinfo=timezone.utc)
# mpegtsmux 가 음수 PTS 를 피하려고 옮기는 기준. 실측 3600002초다.
MUX_OFFSET_NS = 3_600_002_000_000_000


def segment(
    sequence: int,
    *,
    first_pts: int | None = None,
    muxed_pts: int | None = None,
    first_frame_key: bool = True,
) -> Segment:
    return Segment(
        segment_id=sequence % 8,
        sequence=sequence,
        started_at=T0 + timedelta(seconds=sequence),
        ended_at=T0 + timedelta(seconds=sequence + 1),
        duration_ms=1000,
        first_pts=first_pts,
        first_frame_key=first_frame_key,
        path=f'buffer/seg_{sequence % 8:06d}.ts',
        muxed_pts=muxed_pts,
    )


def sane(sequence: int, pts_ns: int) -> Segment:
    """두 값이 모두 있는 정상 조각."""
    return segment(sequence, first_pts=pts_ns, muxed_pts=MUX_OFFSET_NS + pts_ns)


# ----------------------------------------------------------------------
# 동률과 역행
# ----------------------------------------------------------------------


def test_동률은_역행이_아니며_마감을_막지_않는다():
    """이것이 이벤트 17건을 버린 판정이다.

    두 조각이 같은 첫 PTS 를 기록하는 것은 조각 경계 사이에 새 프레임이 밀리지
    않았다는 뜻이지 시간이 되돌아갔다는 뜻이 아니다.
    """
    segments = [sane(1, 1_000_000_000), sane(2, 1_000_000_000), sane(3, 2_000_000_000)]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsRegressions'] == []
    assert report['ok'] is True
    assert report['ptsTies'] == [2], '동률은 조용히 넘기지 않고 따로 보고한다'


def test_진짜_역행은_여전히_막는다():
    """되돌아간 조각을 이어붙이면 재생이 깨진다. 검사 자체는 옳다."""
    segments = [sane(1, 2_000_000_000), sane(2, 1_000_000_000)]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsRegressions'] == [2]
    assert report['ok'] is False


def test_동률이_반복돼도_실패가_아니고_전부_보고된다():
    """정체를 놓치지 않으려면 개수가 보여야 한다."""
    segments = [sane(index, 1_000_000_000) for index in range(1, 5)]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsTies'] == [2, 3, 4]
    assert report['ok'] is True


# ----------------------------------------------------------------------
# 판정 기준 (muxedPts)
# ----------------------------------------------------------------------


def test_muxed_pts가_다_있으면_그것으로_판정한다():
    """스레드 시차가 있어도 조각 순서가 맞아야 한다.

    `firstPts` 는 시차 때문에 뒤 조각이 더 작을 수 있다. 그때도 실제 첫 샘플
    순서(`muxedPts`)가 올바르면 마감은 진행돼야 한다.
    """
    segments = [
        segment(1, first_pts=5_000_000_000, muxed_pts=MUX_OFFSET_NS + 1_000_000_000),
        segment(2, first_pts=4_000_000_000, muxed_pts=MUX_OFFSET_NS + 2_000_000_000),
    ]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsSource'] == 'muxed'
    assert report['ptsRegressions'] == []
    assert report['ok'] is True


def test_muxed_pts가_하나라도_없으면_전부_input_기준으로_내려간다():
    """섞어 쓰면 1시간 오프셋이 가짜 점프·가짜 역행을 만든다.

    옛 인덱스(muxedPts 이전 형식)를 읽는 경로이기도 하다.
    """
    segments = [
        segment(1, first_pts=1_000_000_000, muxed_pts=MUX_OFFSET_NS + 1_000_000_000),
        segment(2, first_pts=2_000_000_000, muxed_pts=None),
    ]

    assert ordering_pts_source(segments) == 'input'

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsSource'] == 'input'
    assert report['ptsRegressions'] == []
    assert report['ptsUnknown'] == [], 'input 기준에서는 두 조각 다 값이 있다'


# ----------------------------------------------------------------------
# 값이 없는 조각
# ----------------------------------------------------------------------


def test_판정할_pts가_없으면_검사가_비었다는_것을_보고한다():
    """종전에는 값이 없으면 조용히 건너뛰어 `ok: true` 와 구별되지 않았다.

    첫 조각 메타데이터가 비는 것만으로 순서 검증이 통째로 무력해지는데, 그것이
    보고서에 드러나지 않으면 「검사를 통과했다」로 읽힌다.
    """
    segments = [segment(1, first_pts=None), segment(2, first_pts=None)]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsUnknown'] == [1, 2]
    assert report['ptsRegressions'] == []
    assert report['ok'] is True, '값이 없는 것 자체는 실패가 아니다 — 드러내기만 한다'


def test_값이_있는_구간의_역행은_값이_없는_이웃과_무관하게_잡힌다():
    segments = [
        sane(1, 2_000_000_000),
        segment(2, first_pts=None, muxed_pts=None),
        sane(3, 1_000_000_000),
    ]

    report = continuity_report(segments, segment_seconds=1)

    assert report['ptsSource'] == 'input', 'muxedPts 가 빠진 조각이 있으므로 내려간다'
    assert report['ptsUnknown'] == [2]
    # 2 는 비교에서 빠지므로 1↔2, 2↔3 은 판정하지 않는다. 인접 비교만 하는 것은
    # 의도다 — 값이 없는 조각을 건너뛰고 1↔3 을 비교하면 그 사이의 리베이스를
    # 역행으로 잘못 잡는다.
    assert report['ptsRegressions'] == []


# ----------------------------------------------------------------------
# 기존 검사가 그대로인지
# ----------------------------------------------------------------------


def test_누락_sequence는_여전히_실패다():
    segments = [sane(1, 1_000_000_000), sane(4, 2_000_000_000)]

    report = continuity_report(segments, segment_seconds=1)

    assert report['missingSequences'] == [2, 3]
    assert report['ok'] is False


def test_첫_조각이_키프레임인지_보고한다():
    segments = [sane(1, 1_000_000_000)]
    assert continuity_report(segments, segment_seconds=1)['firstSegmentIsKeyframe']

    segments = [segment(1, first_pts=1, muxed_pts=1, first_frame_key=False)]
    assert not continuity_report(segments, segment_seconds=1)['firstSegmentIsKeyframe']


def test_빈_목록은_판정_기준을_input으로_둔다():
    """조각이 없으면 `all()` 이 참이라 muxed 로 갈 뻔했다. 비교할 것이 없으므로
    어느 쪽이어도 결과는 같지만, 보고서에 실재하지 않는 기준이 적히면 조사할 때
    잘못된 단서가 된다."""
    assert ordering_pts_source([]) == 'input'

    report = continuity_report([], segment_seconds=1)

    assert report['segmentCount'] == 0
    assert report['ptsSource'] == 'input'

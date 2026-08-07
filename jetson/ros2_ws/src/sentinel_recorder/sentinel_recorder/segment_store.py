"""링 버퍼 조각을 읽고 이벤트로 가져온다 (S15P11A301-123, 명세 32-5).

`sentinel_streaming`의 링 writer가 `index.json`을 쓰고 이 모듈이 읽는다. 두
프로세스의 유일한 접점이며, 파일이라서 어느 쪽이 죽어도 다른 쪽이 계속 돈다.

## 조각을 원자적 복사로 가져가는 이유 — hard link는 여기서 통하지 않는다

명세 32-5는 "hard link 또는 원자적 복사"를 허용하고, 원래 이 모듈은 link를 먼저
시도했다. 근거는 "링이 원본을 지워도 inode가 살아 있다"였는데 **그 전제가 이 링
writer에서는 거짓이다**(S15P11A301-333).

`splitmuxsink`는 `max-files`로 파일을 지우고 새로 만드는 것이 아니라 **같은
inode를 truncate해서 덮어쓴다.** hard link는 inode를 붙잡으므로 아무것도 보호하지
못한다. 실증(2026-08-07, 스택 기동 상태에서 링 조각에 링크를 걸고 14초 대기):

    링크 직후    ino=1188621 nlink=2 size=214696 sha=6d247d945de1899e
    14초 후 원본  ino=1188621        size=197212 sha=caf99e1c4a90dbfc
    14초 후 링크  ino=1188621        size=197212 sha=caf99e1c4a90dbfc

`nlink=2`인데도 링크 파일의 내용이 바뀌었다. "inode 번호가 같다"만으로는 판단할
수 없다 — 파일시스템이 unlink 후 번호를 재활용한 경우와 구별되지 않는다. 위처럼
**링크의 내용을 직접 비교**해야 갈린다.

대가는 105초 이벤트 영상에 **마지막 8초가 13회 반복**된 것이었다(조각 105개,
고유 파일 8개). 파일도 오디오 트랙도 정상이라 겉보기에는 성공이었다.

그래서 복사한다. `.part`에 쓴 뒤 `os.replace`로 옮겨 중간에 죽어도 잘린 `.ts`가
목적지에 남지 않게 한다 — 32-5가 말한 "원자적"이 이것이다.

**"복사하면 디스크를 두 번 쓴다"를 이유로 되돌리지 않는다.** link가 아낀 것은
실제로 아낀 것이 아니었다 — 그 데이터는 파괴되고 있었다. 1500kbps에서 초당 약
190KB이고, 300초 상한(32-5 MAX_EVENT_SECONDS)까지 가도 약 57MB로 pending 상한
580MB 안이다. 조각당 215KB 복사는 밀리초 단위이고 같은 파일명이 재사용되기까지
8초(`ring_segments`)가 있다.

## 시각 기준

`detectedAt`(AI가 준 wall clock)과 조각의 `startedAt`을 맞춘다. 둘 다 진짜 wall
clock이다. 조각의 `startedAt`은 파이프라인 프로세스가 조각 경계에서 `time.time()`
으로 찍은 값이므로 `usb_cam` stamp의 고정 오프셋 문제(32-5 「시각 기준」)와
무관하다.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def parse_utc(value: str) -> datetime:
    """`2026-07-28T04:30:11.180Z` 형식을 읽는다.

    `fromisoformat`은 파이썬 3.10에서 `Z`를 받지 않으므로 치환한다.
    """
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec='milliseconds')
        .replace('+00:00', 'Z')
    )


@dataclass(frozen=True)
class Segment:
    """`index.json`의 조각 한 개."""

    segment_id: int
    sequence: int
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    first_pts: int | None
    first_frame_key: bool
    path: str
    # `mpegtsmux`를 지난 실제 첫 샘플 PTS (S15P11A301-304).
    #
    # `first_pts`와 달리 **이 조각의 첫 프레임 값**이다. 링 writer가 조각 경계에서
    # `first_sample`에서 직접 꺼내므로 스레드 시차가 없다. 순서 판정은 이 값으로
    # 한다 — 이유는 `continuity_report`에 적었다.
    #
    # 기본값을 두는 것은 `muxedPts` 없이 기록된 옛 인덱스를 읽을 수 있어야 하기
    # 때문이다. 그때는 `first_pts`로 판정이 내려간다.
    muxed_pts: int | None = None

    @property
    def filename(self) -> str:
        """링 버퍼에 있는 파일명. `max-files` 때문에 재사용된다."""
        return Path(self.path).name

    @property
    def local_filename(self) -> str:
        """이벤트 디렉터리에서 쓸 파일명.

        **링 버퍼의 파일명을 그대로 쓰면 안 된다.** `splitmuxsink`가 `max-files`로
        파일명을 순환시키기 때문이다. 실측으로 `seg_000000`~`seg_000007`이
        재사용되는 것을 확인했다.

        같은 이름을 쓰면 이미 존재하는 파일을 만나 수집을 건너뛰고, 서로 다른
        조각 수십 개가 같은 8개 파일을 가리키게 된다. 그러면 이어붙인 MP4가 같은
        8초를 반복하는데 **길이와 프레임 수는 맞아서 검증을 통과한다.** 실제로
        68조각 이벤트가 67.9초 2036프레임으로 나와 정상처럼 보였다.

        `sequence`는 링 writer가 단조 증가시키므로 충돌하지 않는다.
        """
        return f'seg_{self.sequence:08d}{Path(self.path).suffix}'

    @classmethod
    def from_index(cls, raw: dict[str, Any]) -> Segment | None:
        """열린 조각(endedAt이 없는 것)은 건너뛴다.

        아직 쓰이는 중인 파일을 가져가면 잘린 조각을 얻는다. 링 writer가 완료된
        조각만 노출하지만, 인덱스 형식이 바뀌어도 여기서 한 번 더 막는다.
        """
        if not raw.get('endedAt') or raw.get('durationMs') is None:
            return None
        try:
            return cls(
                segment_id=int(raw['segmentId']),
                sequence=int(raw.get('sequence', raw['segmentId'])),
                started_at=parse_utc(raw['startedAt']),
                ended_at=parse_utc(raw['endedAt']),
                duration_ms=int(raw['durationMs']),
                first_pts=(
                    int(raw['firstPts']) if raw.get('firstPts') is not None else None
                ),
                first_frame_key=bool(raw.get('firstFrameKey', False)),
                path=str(raw['path']),
                muxed_pts=(
                    int(raw['muxedPts']) if raw.get('muxedPts') is not None else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None


class SegmentStore:
    """`index.json`을 읽고 조각을 이벤트 디렉터리로 가져온다."""

    def __init__(self, buffer_directory: str | Path) -> None:
        self.buffer_directory = Path(buffer_directory)
        self.index_path = self.buffer_directory / 'index.json'

    def read_index(self) -> list[Segment]:
        """완료된 조각을 sequence 순으로 돌려준다.

        인덱스를 읽지 못하면 빈 목록을 준다. 예외를 던지면 노드가 죽고, 녹화가
        멈추는 것보다 이번 주기를 건너뛰는 편이 낫다. 링 writer가 원자적으로
        쓰므로 반쪽 JSON을 볼 일은 없지만, 아직 파일이 없는 시점은 정상이다.
        """
        try:
            raw = json.loads(self.index_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return []

        segments = [
            segment
            for segment in (Segment.from_index(item) for item in raw.get('segments', []))
            if segment is not None
        ]
        segments.sort(key=lambda item: item.sequence)
        return segments

    def segments_covering(
        self, since: datetime, segments: list[Segment] | None = None
    ) -> list[Segment]:
        """`since` 이후를 포함하는 조각을 돌려준다.

        조각이 1초 단위이므로 `since`가 조각 중간에 걸리면 그 조각 전체가
        포함된다. 결과적으로 사전 영상이 3초에서 4초 사이가 된다. VID-03의
        허용오차가 -0초에서 +1초인 것이 이 granularity를 전제한 것이다.

        경계 조건: `ended_at > since`로 판정한다. `started_at >= since`로 하면
        `since`를 담고 있는 조각이 빠져 사전 영상이 3초 미만이 될 수 있다.
        """
        pool = self.read_index() if segments is None else segments
        return [segment for segment in pool if segment.ended_at > since]

    def pre_roll_start(self, detected_at: datetime, pre_seconds: int) -> datetime:
        return detected_at - timedelta(seconds=pre_seconds)

    def collect(self, segment: Segment, destination: Path) -> Path | None:
        """조각을 이벤트 디렉터리로 **복사**한다. 이미 있으면 그대로 둔다.

        hard link를 쓰지 않는 이유는 모듈 주석에 있다 — 링 writer가 같은 inode를
        덮어쓰므로 링크는 보호가 되지 않는다(S15P11A301-333).
        """
        source = self.buffer_directory / segment.filename
        # 목적지는 sequence 기준 이름을 쓴다. 링 버퍼의 파일명은 재사용되므로
        # 그대로 쓰면 서로 다른 조각이 같은 파일을 가리킨다(local_filename 참고).
        target = destination / segment.local_filename
        if target.exists():
            return target
        if not source.exists():
            # 링이 이미 지웠다. 사전 영상 구간이 링 길이보다 오래됐을 때 생긴다.
            return None

        destination.mkdir(parents=True, exist_ok=True)
        # 임시 이름에 쓴 뒤 replace 한다. 복사 중에 죽으면 잘린 .ts 가 목적지에
        # 남고, 먹서는 그것을 정상 조각으로 읽어 이벤트를 깨뜨린다. 32-5 가
        # "원자적 복사"라고 한 것이 이 절차다.
        staging = target.with_name(target.name + '.part')
        try:
            shutil.copy2(source, staging)
            os.replace(staging, target)
        except OSError:
            # 남은 임시 파일은 지운다. 다음 시도가 이름 충돌로 막히지 않게 한다.
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return target


def ordering_pts_source(segments: list[Segment]) -> str:
    """순서 판정에 쓸 PTS 출처를 고른다 (S15P11A301-304).

    `muxedPts`가 **모든** 조각에 있을 때만 그것을 쓴다. 하나라도 없으면 전부
    `firstPts`로 내려간다. 섞어 쓰면 안 되는 이유는 두 값의 **기준점이 다르기**
    때문이다 — `muxedPts`는 `mpegtsmux`가 음수 PTS를 피하려고 옮긴 기준이라 1시간
    오프셋(실측 3600002초)이 붙어 있다. 한 조각만 `firstPts`로 비교되면 그 경계에서
    1시간짜리 가짜 점프나 가짜 역행이 나온다.
    """
    if segments and all(segment.muxed_pts is not None for segment in segments):
        return 'muxed'
    return 'input'


def continuity_report(segments: list[Segment], segment_seconds: int) -> dict[str, Any]:
    """조각 목록의 순서와 누락을 검사한다 (32-5 「이벤트 종료와 MP4 생성」).

    `sequence`가 연속인가. 링 writer가 단조 증가시키므로 구멍은 조각 누락이다.
    non-leaky 큐가 프레임을 버리지 않아도 디스크 쓰기 지연이 조각을 잃을 수 있다
    (PoC-B 조건 6이 측정하려던 것).

    조각의 첫 PTS가 **감소하지 않는가**. 감소하면 PTS 리베이스가 조각 경계가 아닌
    곳에서 일어난 것이고, 이어붙인 MP4의 재생이 깨진다.

    첫 조각이 키프레임으로 시작하는지도 함께 본다. 아니면 이벤트 첫 화면이
    깨진다.

    ## 판정 기준을 `muxedPts`로 옮겼다 (S15P11A301-304)

    종전에는 `firstPts`를 `<=`로 비교해 **동률을 역행으로 판정**했고, 그것이 실제로
    이벤트 영상을 버렸다. 2026-08-06 실측에서 젯슨 `pending` 39건 중 17건이
    `RECORDING_FAILED_PTS_REGRESSION`이었고, 같은 임무에서 세 번 중 두 번 MP4를
    잃었다.

    동률이 나오는 것이 정상이기 때문이다. `firstPts`는 링 writer가 조각 경계에서
    읽는 **「그 순간 마지막으로 밀어 넣은 입력 PTS」**이지 이 조각의 첫 프레임 값이
    아니다(32-5의 「구현이 추가한 필드」가 그 오차를 33ms로 적어 두었다). 조각이
    열리는 사이에 새 프레임이 밀리지 않으면 이웃 조각이 같은 값을 기록한다. 그때
    로그의 역행 sequence가 3 간격의 규칙적 패턴으로 나오는 것이 이 해석의 증거다.
    같은 기간 `PTS 리베이스` 경고는 0건이었다 — 진짜 역행은 없었다.

    그래서 두 가지를 바꿨다.

    1. 판정에 `muxedPts`를 쓴다. 링 writer가 `format-location-full`의 `first_sample`
       에서 직접 꺼내는 **그 조각의 첫 샘플 PTS**라 스레드 시차가 없다.
       `firstPts`는 그대로 남긴다 — 3초 사전 영상을 찾는 용도에는 입력 기준이 맞고,
       그것이 32-5가 그 필드를 입력 기준으로 둔 이유다.
    2. 동률은 역행이 아니다(`<`로 비교). 대신 `ptsTies`로 따로 보고한다. 조용히
       넘기면 인코더가 실제로 정체된 경우를 놓친다.

    `ptsUnknown`을 함께 내는 이유는 **검사가 무력화된 것을 보이게** 하기 위해서다.
    값이 없는 조각은 비교에서 빠지므로, 전부 비어 있으면 순서 검증이 사실상 꺼진
    것인데 종전에는 그것이 `ok: true`와 구별되지 않았다.
    """
    pts_source = ordering_pts_source(segments)

    def ordering_pts(segment: Segment) -> int | None:
        return segment.muxed_pts if pts_source == 'muxed' else segment.first_pts

    missing_sequences: list[int] = []
    pts_regressions: list[int] = []
    pts_ties: list[int] = []

    for previous, current in zip(segments, segments[1:]):
        gap = current.sequence - previous.sequence
        if gap > 1:
            missing_sequences.extend(
                range(previous.sequence + 1, current.sequence)
            )
        before, after = ordering_pts(previous), ordering_pts(current)
        if before is None or after is None:
            continue
        if after < before:
            pts_regressions.append(current.sequence)
        elif after == before:
            pts_ties.append(current.sequence)

    total_ms = sum(segment.duration_ms for segment in segments)
    expected_ms = len(segments) * segment_seconds * 1000

    return {
        'segmentCount': len(segments),
        'missingSequences': missing_sequences,
        'ptsRegressions': pts_regressions,
        # 아래 둘은 마감을 막지 않는다. 판단 근거로만 남긴다.
        'ptsTies': pts_ties,
        'ptsUnknown': [
            segment.sequence for segment in segments if ordering_pts(segment) is None
        ],
        'ptsSource': pts_source,
        'firstSegmentIsKeyframe': bool(segments and segments[0].first_frame_key),
        'totalDurationMs': total_ms,
        # 조각 길이가 1초에서 크게 벗어나면 인코더 IDR 주기나 디스크 지연을
        # 의심해야 한다. 비율로 남겨 판단은 호출자에게 맡긴다.
        'durationRatio': (total_ms / expected_ms) if expected_ms else None,
        'ok': not missing_sequences and not pts_regressions,
    }

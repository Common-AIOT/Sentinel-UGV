"""링 버퍼 조각을 읽고 이벤트로 가져온다 (S15P11A301-123, 명세 32-5).

`sentinel_streaming`의 링 writer가 `index.json`을 쓰고 이 모듈이 읽는다. 두
프로세스의 유일한 접점이며, 파일이라서 어느 쪽이 죽어도 다른 쪽이 계속 돈다.

## 조각을 hard link로 가져가는 이유

링 버퍼는 `max-files`로 오래된 조각을 지운다. 복사가 끝나기를 기다리면 그 사이에
원본이 사라질 수 있고, 복사하면 디스크를 두 번 쓴다. hard link는 즉시 끝나고
링이 원본을 지워도 inode가 살아 있다. 명세 32-5가 "hard link 또는 원자적 복사"를
지정한 이유다.

같은 볼륨이 아니면 hard link가 안 되므로 복사로 폴백한다. 32-5는 버퍼와 pending을
같은 볼륨에 두라고 했으니 정상 구성에서는 link가 쓰인다.

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
        """조각을 이벤트 디렉터리로 가져온다. 이미 있으면 그대로 둔다.

        hard link를 먼저 시도하고, 다른 볼륨이면 복사한다.
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
        try:
            os.link(source, target)
        except OSError:
            try:
                shutil.copy2(source, target)
            except OSError:
                return None
        return target


def continuity_report(segments: list[Segment], segment_seconds: int) -> dict[str, Any]:
    """조각 목록의 순서와 누락을 검사한다 (32-5 「이벤트 종료와 MP4 생성」).

    두 가지를 본다.

    `sequence`가 연속인가. 링 writer가 단조 증가시키므로 구멍은 조각 누락이다.
    non-leaky 큐가 프레임을 버리지 않아도 디스크 쓰기 지연이 조각을 잃을 수 있다
    (PoC-B 조건 6이 측정하려던 것).

    `firstPts`가 단조 증가하는가. 되돌아가면 PTS 리베이스가 조각 경계가 아닌
    곳에서 일어난 것이고, 이어붙인 MP4의 재생이 깨진다.

    첫 조각이 키프레임으로 시작하는지도 함께 본다. 아니면 이벤트 첫 화면이
    깨진다.
    """
    missing_sequences: list[int] = []
    pts_regressions: list[int] = []

    for previous, current in zip(segments, segments[1:]):
        gap = current.sequence - previous.sequence
        if gap > 1:
            missing_sequences.extend(
                range(previous.sequence + 1, current.sequence)
            )
        if (
            previous.first_pts is not None
            and current.first_pts is not None
            and current.first_pts <= previous.first_pts
        ):
            pts_regressions.append(current.sequence)

    total_ms = sum(segment.duration_ms for segment in segments)
    expected_ms = len(segments) * segment_seconds * 1000

    return {
        'segmentCount': len(segments),
        'missingSequences': missing_sequences,
        'ptsRegressions': pts_regressions,
        'firstSegmentIsKeyframe': bool(segments and segments[0].first_frame_key),
        'totalDurationMs': total_ms,
        # 조각 길이가 1초에서 크게 벗어나면 인코더 IDR 주기나 디스크 지연을
        # 의심해야 한다. 비율로 남겨 판단은 호출자에게 맡긴다.
        'durationRatio': (total_ms / expected_ms) if expected_ms else None,
        'ok': not missing_sequences and not pts_regressions,
    }

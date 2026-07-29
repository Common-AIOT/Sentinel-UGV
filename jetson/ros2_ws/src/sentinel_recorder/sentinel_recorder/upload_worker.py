"""업로드 대기 이벤트를 올린다 (S15P11A301-124, 명세 31-7·31-10).

`sentinel_recorder`의 `recording_manager`가 `pending/<encounterId>/`에 남긴 것을
읽어 올리고, 성공하면 `report.json`의 `uploadState`를 `AVAILABLE`로 바꾼다. 그러면
`PendingStore`가 상한을 넘길 때 그것부터 지울 수 있다(32-5).

## 재시도 정책 (31-10)

    이벤트 영상  로컬 파일과 업로드 작업 저장 → 복구 후 Presigned URL 재발급 후 업로드

Presigned URL을 캐시하지 않고 매 시도마다 재발급한다. 유효기간이 짧아 옛 URL은
만료된다.

지수 백오프는 MQTT와 같은 값을 쓴다(1, 2, 4, 8, 최대 30초). 다른 값을 쓸 이유가
없고, 운영자가 로그에서 같은 리듬을 보는 편이 낫다.

## 영상과 썸네일을 따로 올린다

이벤트 하나에 객체가 둘이다. 썸네일이 실패해도 영상은 `AVAILABLE`로 둔다. 관제
목록에 썸네일이 없는 항목이 나오는 것이 영상 자체를 못 보는 것보다 낫다. 32-5가
공간 부족 시에도 "썸네일과 JSON 보고서는 남긴다"고 한 것과 같은 우선순위다.

## 파일을 지우지 않는다

업로드 성공 후에도 로컬 파일을 남긴다. 지우는 것은 `PendingStore.enforce_cap`의
책임이고, 그쪽이 상한을 넘을 때만 오래된 것부터 지운다. 업로드 직후 지우면 망이
불안정할 때 재확인할 방법이 사라진다.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .event_finalizer import (
    FINAL_NAME,
    REPORT_NAME,
    THUMBNAIL_NAME,
    read_report,
    write_report,
)
from .pending_store import (
    UPLOAD_STATE_AVAILABLE,
    UPLOAD_STATE_PENDING,
    PendingStore,
)
from .upload_client import (
    KIND_THUMBNAIL,
    KIND_VIDEO,
    UploadClient,
    UploadError,
    UploadTarget,
)

BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)

# 썸네일 mediaId를 영상 mediaId에서 파생할 때 쓰는 이름공간.
#
# 백엔드가 mediaId를 UUID로 받고 media_assets.id 가 UUID PRIMARY KEY다(31-10).
# 그래서 `{mediaId}_thumb` 같은 문자열을 쓸 수 없다. 실물 업로드에서 400으로
# 드러났다(S15P11A301-124).
#
# uuid5를 쓰는 이유는 결정적이기 때문이다. 재시도해도 같은 값이 나오므로 서버가
# 중복 등록을 막을 수 있다. uuid4로 매번 새로 만들면 재시도마다 새 행이 생긴다.
THUMBNAIL_NAMESPACE = uuid.UUID('6f9c1a52-3f4e-4b8a-9d21-7c5e0b8f4a13')


def thumbnail_media_id(video_media_id: str) -> str:
    """영상 mediaId에서 썸네일 mediaId를 파생한다. 같은 입력이면 같은 출력이다."""
    return str(uuid.uuid5(THUMBNAIL_NAMESPACE, f'{video_media_id}:thumbnail'))


CONTENT_TYPES = {
    FINAL_NAME: 'video/mp4',
    THUMBNAIL_NAME: 'image/jpeg',
}


@dataclass
class AttemptState:
    """이벤트별 재시도 상태. 프로세스가 살아 있는 동안만 유지한다.

    디스크에 남기지 않는 이유는 `report.json`의 `uploadState`가 이미 영속 상태이기
    때문이다. 재시작하면 백오프가 처음부터 시작하는데, 그것이 오히려 낫다. 재시작은
    보통 상황이 바뀐 뒤이므로 즉시 한 번 시도해 보는 편이 빠르다.
    """

    failures: int = 0
    next_attempt_at: float = 0.0
    last_reason: str = ''
    permanent: bool = False


@dataclass
class UploadStats:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped_permanent: int = 0
    details: list[str] = field(default_factory=list)


class UploadWorker:
    """pending 디렉터리를 훑어 업로드한다. ROS를 모른다."""

    def __init__(
        self,
        pending: PendingStore,
        client: UploadClient,
        *,
        robot_id: str = 'SENTINEL-01',
        skip_complete: bool = False,
        max_per_cycle: int = 2,
    ) -> None:
        self.pending = pending
        self.client = client
        self.robot_id = robot_id
        self.skip_complete = skip_complete
        # 한 주기에 올리는 이벤트 수를 제한한다. 망이 살아난 직후 수십 건을 한꺼번에
        # 올리면 Wi-Fi를 점유해 WebRTC 스트리밍의 지연이 튄다. 관제 영상이 우선이다.
        self.max_per_cycle = max(1, max_per_cycle)
        self._attempts: dict[str, AttemptState] = {}

    # ------------------------------------------------------------------
    # 대상 선정
    # ------------------------------------------------------------------

    def _due(self, name: str, now: float) -> bool:
        state = self._attempts.get(name)
        if state is None:
            return True
        if state.permanent:
            return False
        return now >= state.next_attempt_at

    def _record_failure(self, name: str, error: UploadError, now: float) -> None:
        state = self._attempts.setdefault(name, AttemptState())
        state.last_reason = error.reason
        if not error.retryable:
            # 재시도해도 같은 결과다. 계약 불일치나 미구현 엔드포인트가 여기 온다.
            # 백오프로 감추면 로그만 반복되고 원인을 못 본다.
            state.permanent = True
            return
        state.failures += 1
        delay = BACKOFF_SECONDS[min(state.failures - 1, len(BACKOFF_SECONDS) - 1)]
        state.next_attempt_at = now + delay

    def _clear(self, name: str) -> None:
        self._attempts.pop(name, None)

    def reset_permanent(self) -> int:
        """영구 실패 표시를 지운다. 백엔드가 고쳐진 뒤 다시 시도할 때 쓴다."""
        count = sum(1 for state in self._attempts.values() if state.permanent)
        self._attempts = {
            name: state
            for name, state in self._attempts.items()
            if not state.permanent
        }
        return count

    # ------------------------------------------------------------------
    # 한 주기
    # ------------------------------------------------------------------

    def run_once(self, now: float | None = None) -> UploadStats:
        now = time.monotonic() if now is None else now
        stats = UploadStats()

        for event in self.pending.scan():
            if stats.attempted >= self.max_per_cycle:
                break
            if event.uploaded:
                continue
            if not event.ready_for_upload:
                # 영상이 없거나(DISK_FULL·CORRUPT로 마감), 아직 마무리 중이다.
                # 마무리 중인 것을 집으면 체크섬이 없어 영구 실패로 떨어진다
                # (PendingEvent.ready_for_upload 참고). 다음 주기에 다시 본다.
                continue

            name = event.directory.name
            if not self._due(name, now):
                if self._attempts.get(name, AttemptState()).permanent:
                    stats.skipped_permanent += 1
                continue

            stats.attempted += 1
            try:
                self._upload_event(event.directory)
            except UploadError as error:
                self._record_failure(name, error, now)
                stats.failed += 1
                stats.details.append(f'{name[:8]} {error.reason} {error.detail[:80]}')
            else:
                self._clear(name)
                stats.succeeded += 1
                stats.details.append(f'{name[:8]} 업로드 완료')

        return stats

    # ------------------------------------------------------------------
    # 이벤트 하나
    # ------------------------------------------------------------------

    def _read_report(self, directory: Path) -> dict[str, Any]:
        """보고서를 읽는다. 읽기 실패는 **재시도 가능**으로 본다.

        전에는 `retryable=False`였고, 그것이 이벤트를 영구히 잃는 경로였다.
        `recording_manager`가 상태 전이로 보고서를 갱신하는 순간 우리가 읽으면 잘린
        JSON을 본다. 그 한 번의 겹침으로 이벤트가 영구 실패 표시를 받고 다시는
        업로드되지 않았다.

        `write_report`가 원자적 교체를 쓰므로 이제 겹침 자체가 사라졌지만, 여기도
        재시도 가능으로 둔다. 방어가 하나면 그것이 깨질 때 이벤트가 사라진다.
        보고서를 못 읽는 것은 대개 일시적이고, 정말 손상됐다면 백오프가 30초까지
        벌어지며 로그에 계속 남는다.
        """
        try:
            return read_report(directory / REPORT_NAME)
        except (OSError, json.JSONDecodeError) as error:
            raise UploadError('REPORT_UNREADABLE', str(error)[:150])

    def _target(self, directory: Path, filename: str, kind: str, sha256: str) -> UploadTarget:
        path = directory / filename
        return UploadTarget(
            path=path,
            kind=kind,
            sha256=sha256,
            size_bytes=path.stat().st_size,
            content_type=CONTENT_TYPES.get(filename, 'application/octet-stream'),
        )

    def _upload_event(self, directory: Path) -> None:
        report = self._read_report(directory)
        encounter_id = str(report.get('encounterId') or directory.name)
        media_id = str(report.get('mediaId') or directory.name)
        media = report.get('media') or {}
        sha256 = str(media.get('sha256') or '')

        if not sha256:
            # 여기 오면 `ready_for_upload`가 걸러야 했던 것이 새어 들어온 것이다.
            # 영구 실패로 두지 않는다. 마무리가 진행 중이었다면 다음 주기에 성공하고,
            # 정말 손상됐다면 백오프가 30초까지 벌어지며 로그에 계속 남는다. 한 번의
            # 오판으로 이벤트 영상을 영원히 잃는 쪽이 훨씬 나쁘다.
            raise UploadError(
                'CHECKSUM_MISSING',
                '보고서에 media.sha256이 없다. 마무리가 끝나지 않았을 수 있다',
            )

        video = self._target(directory, FINAL_NAME, KIND_VIDEO, sha256)
        recorded = {
            'detectedAt': report.get('detectedAt'),
            'preRollSeconds': (report.get('coverage') or {}).get('preRollSeconds'),
            'postRollSeconds': (report.get('coverage') or {}).get('postRollSeconds'),
            'endReason': report.get('endReason'),
            'personCount': report.get('personCount'),
        }

        outcome = self.client.upload(
            encounter_id=encounter_id,
            media_id=media_id,
            target=video,
            suggested_key=self._suggested_key(encounter_id, FINAL_NAME),
            duration_seconds=media.get('durationSeconds'),
            recorded=recorded,
            skip_complete=self.skip_complete,
        )

        # 썸네일은 실패해도 영상을 AVAILABLE로 둔다. 목록에 썸네일이 없는 항목이
        # 나오는 것이 영상을 못 보는 것보다 낫다.
        thumbnail_key: str | None = None
        thumbnail_path = directory / THUMBNAIL_NAME
        if thumbnail_path.exists():
            try:
                from .event_finalizer import sha256_of

                thumbnail = self._target(
                    directory,
                    THUMBNAIL_NAME,
                    KIND_THUMBNAIL,
                    sha256_of(thumbnail_path),
                )
                thumbnail_outcome = self.client.upload(
                    encounter_id=encounter_id,
                    media_id=thumbnail_media_id(media_id),
                    target=thumbnail,
                    suggested_key=self._suggested_key(encounter_id, THUMBNAIL_NAME),
                    skip_complete=self.skip_complete,
                )
                thumbnail_key = thumbnail_outcome.object_key
            except (UploadError, OSError):
                thumbnail_key = None

        self._mark_available(directory, outcome.object_key, thumbnail_key)

    def _suggested_key(self, encounter_id: str, filename: str) -> str:
        """서버가 무시해도 되는 힌트다(31-11).

        `robotId/encounterId/파일명`으로 두면 사람이 스토리지를 볼 때 찾기 쉽다.
        """
        return f'{self.robot_id}/{encounter_id}/{filename}'

    def _mark_available(
        self, directory: Path, object_key: str, thumbnail_key: str | None
    ) -> None:
        report_path = directory / REPORT_NAME
        try:
            report = read_report(report_path)
        except (OSError, json.JSONDecodeError):
            return

        report['uploadState'] = (
            UPLOAD_STATE_PENDING if self.skip_complete else UPLOAD_STATE_AVAILABLE
        )
        report['storage'] = {
            'objectKey': object_key,
            'thumbnailKey': thumbnail_key,
            # skip_complete면 서버가 완료를 모르므로 그것을 남긴다. 나중에 왜
            # AVAILABLE이 아닌지 헷갈리지 않게 한다.
            'completeCalled': not self.skip_complete,
        }
        write_report(report_path, report)

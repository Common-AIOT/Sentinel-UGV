"""업로드 대기 이벤트 보관과 상한 관리 (S15P11A301-123, 명세 32-5).

TBD-VID-001에서 정한 상한을 집행한다.

    pending 보존 상한 30분 분량(약 560MB)
    초과 시 업로드 완료분부터 오래된 순으로 삭제
    미업로드분만으로 상한을 넘으면 RECORDING_FAILED_DISK_FULL

## 왜 상한이 필요한가

디스크를 채우는 것은 링 버퍼가 아니라 pending이다. 링 버퍼는 1초 조각 8개를
순환하므로 점유량이 약 2.5MB로 고정된다. 2.5Mbps는 시간당 1.1GB의 쓰기
*처리량*이지 누적량이 아니다. 누적하는 것은 업로드를 기다리는 이벤트 MP4뿐이고,
망이 정상이면 업로드 직후 삭제되므로 거의 비어 있다.

즉 상한은 **망 단절이 길어질 때만** 의미가 있다. microSD 여유 13GB에 560MB는
4% 수준이라 안전 마진이 크다.

## 삭제 순서

32-5는 "오래된 업로드 완료 파일부터 삭제해 공간을 확보한다"고 정했다. 업로드가
끝난 것은 EC2에 사본이 있으므로 지워도 잃는 것이 없다. 미업로드분은 마지막까지
지키고, 그것만으로 상한을 넘으면 새 이벤트의 영상을 포기한다.

영상을 포기해도 **썸네일과 보고서는 남긴다.** 32-5가 명시했다. 관제에서 "이 시각에
사람을 발견했으나 영상이 없다"를 볼 수 있어야 하고, 그것은 영상 파일보다 훨씬
작다.

업로드 상태는 `sentinel_recorder`가 쓰지 않는다. S15P11A301-124가 업로드에
성공하면 `report.json`의 `uploadState`를 `AVAILABLE`로 바꾼다. 여기서는 그 값을
읽기만 한다.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .event_finalizer import FINAL_NAME, REPORT_NAME, THUMBNAIL_NAME

UPLOAD_STATE_PENDING = 'UPLOAD_PENDING'
UPLOAD_STATE_AVAILABLE = 'AVAILABLE'
MEDIA_STATE_DISK_FULL = 'RECORDING_FAILED_DISK_FULL'

# 32-8이 정한 목표 비트레이트. 상한을 바이트로 환산하는 데 쓴다.
DEFAULT_BITRATE_KBPS = 2500


def bytes_for_seconds(seconds: int, bitrate_kbps: int = DEFAULT_BITRATE_KBPS) -> int:
    return int(seconds * bitrate_kbps * 1000 / 8)


@dataclass
class PendingEvent:
    directory: Path
    media_bytes: int
    total_bytes: int
    upload_state: str
    finalized_at: str | None

    @property
    def uploaded(self) -> bool:
        return self.upload_state == UPLOAD_STATE_AVAILABLE

    @property
    def has_media(self) -> bool:
        return (self.directory / FINAL_NAME).exists()


class PendingStore:
    """pending 디렉터리를 훑고 상한을 집행한다."""

    def __init__(
        self,
        pending_directory: str | Path,
        max_pending_seconds: int = 1800,
        bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
    ) -> None:
        self.directory = Path(pending_directory)
        self.max_pending_seconds = max_pending_seconds
        self.bitrate_kbps = bitrate_kbps

    @property
    def cap_bytes(self) -> int:
        return bytes_for_seconds(self.max_pending_seconds, self.bitrate_kbps)

    def prepare(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def _directory_bytes(self, directory: Path) -> int:
        total = 0
        for path in directory.rglob('*'):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def scan(self) -> list[PendingEvent]:
        """오래된 순으로 돌려준다.

        `finalizedAt`이 없으면 디렉터리 mtime을 쓴다. 보고서를 읽지 못한
        디렉터리도 목록에 넣어야 공간 계산이 맞는다.
        """
        if not self.directory.is_dir():
            return []

        events: list[tuple[float, PendingEvent]] = []
        for child in self.directory.iterdir():
            if not child.is_dir():
                continue
            report_path = child / REPORT_NAME
            upload_state = UPLOAD_STATE_PENDING
            finalized_at: str | None = None
            try:
                report = json.loads(report_path.read_text(encoding='utf-8'))
                upload_state = str(report.get('uploadState', UPLOAD_STATE_PENDING))
                finalized_at = report.get('finalizedAt')
            except (OSError, json.JSONDecodeError):
                pass

            media = child / FINAL_NAME
            media_bytes = media.stat().st_size if media.exists() else 0
            sort_key = child.stat().st_mtime
            events.append(
                (
                    sort_key,
                    PendingEvent(
                        directory=child,
                        media_bytes=media_bytes,
                        total_bytes=self._directory_bytes(child),
                        upload_state=upload_state,
                        finalized_at=finalized_at,
                    ),
                )
            )

        events.sort(key=lambda item: item[0])
        return [event for _, event in events]

    def total_bytes(self) -> int:
        return sum(event.total_bytes for event in self.scan())

    # ------------------------------------------------------------------
    # 상한 집행
    # ------------------------------------------------------------------

    def enforce_cap(self, incoming_bytes: int = 0) -> dict[str, Any]:
        """상한을 넘지 않게 정리한다.

        `incoming_bytes`는 지금 저장하려는 이벤트의 크기다. 저장 전에 부르면
        공간을 미리 확보할 수 있다.

        돌려주는 값의 `admitted`가 False면 호출자는 영상을 포기하고
        `RECORDING_FAILED_DISK_FULL`로 기록해야 한다.
        """
        events = self.scan()
        current = sum(event.total_bytes for event in events)
        cap = self.cap_bytes
        removed: list[str] = []
        freed = 0

        # 1단계. 업로드 완료분의 영상을 오래된 순으로 지운다. 보고서와 썸네일은
        # 남긴다. EC2에 사본이 있으므로 영상만 지우면 된다.
        for event in events:
            if current + incoming_bytes <= cap:
                break
            if not event.uploaded or not event.has_media:
                continue
            media = event.directory / FINAL_NAME
            try:
                size = media.stat().st_size
                media.unlink()
            except OSError:
                continue
            current -= size
            freed += size
            removed.append(f'{event.directory.name}/{FINAL_NAME}')

        # 2단계. 그래도 넘으면 업로드 완료 디렉터리를 통째로 지운다. 보고서까지
        # 지우는 것은 EC2에 이미 등록됐다는 뜻이므로 손실이 아니다.
        for event in events:
            if current + incoming_bytes <= cap:
                break
            if not event.uploaded:
                continue
            if not event.directory.exists():
                continue
            try:
                size = self._directory_bytes(event.directory)
                shutil.rmtree(event.directory)
            except OSError:
                continue
            current -= size
            freed += size
            removed.append(f'{event.directory.name}/')

        admitted = current + incoming_bytes <= cap
        return {
            'admitted': admitted,
            'capBytes': cap,
            'usedBytes': current,
            'incomingBytes': incoming_bytes,
            'freedBytes': freed,
            'removed': removed,
            # 미업로드분만으로 상한을 넘은 경우. 32-5의 마지막 예외다.
            'blockedByUnuploaded': not admitted,
        }

    def mark_disk_full(self, directory: Path, detail: str = '') -> None:
        """영상을 포기하고 썸네일·보고서만 남긴다 (32-5).

        관제에서 "이 시각에 사람을 발견했으나 영상이 없다"를 볼 수 있어야 한다.
        보고서를 지우면 그 사실 자체가 사라진다.
        """
        media = directory / FINAL_NAME
        media.unlink(missing_ok=True)
        for stray in directory.glob('*.ts'):
            stray.unlink(missing_ok=True)
        for stray in directory.glob('*.partial.mp4'):
            stray.unlink(missing_ok=True)

        report_path = directory / REPORT_NAME
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            report = {'schemaVersion': '1.0'}
        report['mediaState'] = MEDIA_STATE_DISK_FULL
        report['mediaStateDetail'] = detail
        media_block = report.get('media')
        if isinstance(media_block, dict):
            media_block['path'] = None
            media_block['sha256'] = None
            media_block['sizeBytes'] = 0
            # 썸네일은 남기므로 참조를 유지한다.
            media_block.setdefault('thumbnail', THUMBNAIL_NAME)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    def cleanup_segments(self, directory: Path) -> int:
        """MP4를 만든 뒤 hard link한 조각을 지운다.

        지우지 않으면 이벤트마다 조각이 쌓여 상한 계산이 MP4의 두 배가 된다.
        hard link이므로 링 버퍼의 원본에는 영향이 없다.
        """
        removed = 0
        for path in list(directory.glob('*.ts')) + list(directory.glob('segments.txt')):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

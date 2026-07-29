"""JSONL 로그 작성.

UTF-8, 한 줄당 하나의 JSON 객체, flush 및 파일 예외 처리(AGENTS.md §10, §17).
프레임 로그와 이벤트 로그를 별도 파일로 분리한다(§17).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import TracebackType
from typing import Any


class JsonlWriter:
    """한 파일에 대한 JSONL 기록기."""

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._fp = None
        if not enabled:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = path.open("w", encoding="utf-8")
        except OSError as exc:
            # 로그를 못 쓴다고 파이프라인 전체를 죽이지 않는다. 대신 반드시 알린다.
            print(f"[logger] 로그 파일을 열 수 없습니다: {path} ({exc})", file=sys.stderr)
            self.enabled = False

    def write(self, record: dict[str, Any]) -> None:
        if not self.enabled or self._fp is None:
            return
        try:
            self._fp.write(json.dumps(record, ensure_ascii=False) + "\n")
            # 중간에 프로세스가 죽어도 기록이 남도록 즉시 flush한다.
            self._fp.flush()
        except (OSError, TypeError, ValueError) as exc:
            print(f"[logger] 로그 기록 실패: {exc}", file=sys.stderr)

    def close(self) -> None:
        if self._fp is not None:
            try:
                self._fp.close()
            except OSError:
                pass
            self._fp = None

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class PipelineLogger:
    """프레임 로그와 이벤트 로그를 함께 관리한다."""

    def __init__(
        self,
        output_dir: Path,
        *,
        frames_filename: str = "frames.jsonl",
        events_filename: str = "events.jsonl",
        write_frame_log: bool = False,
    ) -> None:
        self.output_dir = output_dir
        self.frames = JsonlWriter(output_dir / frames_filename, enabled=write_frame_log)
        self.events = JsonlWriter(output_dir / events_filename, enabled=True)

    def log_frame(self, record: dict[str, Any]) -> None:
        self.frames.write(record)

    def log_event(self, record: dict[str, Any]) -> None:
        self.events.write(record)

    def close(self) -> None:
        self.frames.close()
        self.events.close()

    def __enter__(self) -> "PipelineLogger":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

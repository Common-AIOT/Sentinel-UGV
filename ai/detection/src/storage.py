"""이벤트 이미지 저장.

출력 디렉터리 생성, 파일명 충돌 방지, 원본 프레임 수정 금지(AGENTS.md §10, §18).
파일명은 안전한 timestamp 기반으로 만든다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


class EventImageStore:
    def __init__(self, events_dir: Path) -> None:
        self.events_dir = events_dir
        self.events_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _timestamp_name() -> str:
        # 콜론은 Windows 파일명에 쓸 수 없으므로 ISO 8601 대신 안전한 형식을 쓴다.
        now = datetime.now(timezone.utc)
        return now.strftime("%Y%m%d_%H%M%S_") + f"{now.microsecond // 1000:03d}"

    def _unique_path(self, stem: str) -> Path:
        """같은 밀리초에 여러 이벤트가 나도 덮어쓰지 않도록 접미사를 붙인다."""
        path = self.events_dir / f"{stem}.jpg"
        if not path.exists():
            return path
        for i in range(1, 1000):
            candidate = self.events_dir / f"{stem}_{i:03d}.jpg"
            if not candidate.exists():
                return candidate
        # 극단적인 경우에도 덮어쓰지 않도록 UUID로 회피한다.
        import uuid

        return self.events_dir / f"{stem}_{uuid.uuid4().hex[:8]}.jpg"

    def save(self, frame: np.ndarray, *, track_id: int | None = None) -> Path | None:
        """이벤트 프레임을 저장하고 경로를 반환한다. 실패 시 None."""
        suffix = f"_track{track_id}" if track_id is not None else ""
        path = self._unique_path(self._timestamp_name() + suffix)
        try:
            # imwrite는 프레임을 수정하지 않지만, 호출부에서 overlay를 그릴 때
            # 반드시 복사본을 넘겨야 한다(원본 프레임 수정 금지).
            ok = cv2.imwrite(str(path), frame)
        except cv2.error as exc:
            print(f"[storage] 이벤트 이미지 저장 실패: {exc}", file=sys.stderr)
            return None
        if not ok:
            print(f"[storage] 이벤트 이미지 저장 실패: {path}", file=sys.stderr)
            return None
        return path

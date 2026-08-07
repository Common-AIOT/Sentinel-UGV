"""이벤트 이미지 저장.

출력 디렉터리 생성, 파일명 충돌 방지, 원본 프레임 수정 금지(AGENTS.md §10, §18).
파일명은 안전한 timestamp 기반으로 만든다.

## 보존 상한 (S15P11A301-265)

이벤트 쿨다운(`event_cooldown_seconds`)은 **트랙별**이다(`persistence.py`). 사람
1명당 15초이므로 6명이 상시 잡히는 장면에서는 24 이벤트/분이 되고, 8시간이면
1만 장이 넘는다. 실제로 run 하나가 1.2G 까지 커져 디스크가 찼다.

**끄는 것은 답이 아니다** — 이 이미지는 명세 31-5 봉투의 `eventImage` 가 가리키는
증빙이며, 없으면 요구조자 발견 근거가 사라진다. 그래서 상한을 두고 오래된 것부터
버린다.

**개수가 아니라 바이트가 주 기준이다.** 장당 크기가 장면 복잡도와 overlay 유무에
따라 달라지므로, 개수로 묶으면 총량이 장면에 따라 다시 흔들린다. 막고 싶은 것이
디스크이므로 디스크를 직접 잰다. 개수 상한은 보조로 함께 둘 수 있다.

장당 크기 실측은 **165KB**(1280x720, `cv2.imwrite` 기본 화질, 실물 프레임 12장의
중앙값)이며 기본 상한 500MB 는 약 3,100장이다. 값과 근거는 설정 파일 주석에 있다.

상한의 목적은 "며칠 치를 남긴다" 가 아니라 **"장면이 어떻든 디스크가 차지 않는다"**
다. 보존 시간은 사람 수에 따라 2시간에서 며칠까지 달라진다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 주석 평가용. 런타임에는 numpy 없이도 import 된다.
    import numpy as np


class EventImageStore:
    def __init__(
        self,
        events_dir: Path,
        *,
        max_bytes: int | None = None,
        max_files: int | None = None,
    ) -> None:
        """`max_bytes`·`max_files` 가 None 이면 상한을 두지 않는다(종전 동작)."""
        self.events_dir = events_dir
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.events_dir.mkdir(parents=True, exist_ok=True)
        # 기동 시 한 번 정리한다. save() 안에서만 검사하면 **이전 run 이 남긴
        # 초과분이 그대로 남아** 상한이 무의미해진다.
        self.prune()

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
        """이벤트 프레임을 저장하고 경로를 반환한다. 실패 시 None.

        cv2 는 여기서 import 한다. 모듈 최상단에서 하면 **보존 상한 시험까지
        cv2 를 요구하게 된다** — 상한 판정은 파일 이름과 크기만 보는 순수
        로직이라 CI(python:3.10-alpine, pytest 만 설치)에서 돌릴 수 있어야
        하는데, import 사슬 때문에 수집 단계에서 죽었다. 저장 경로에서만
        인코더가 필요하므로 필요한 지점으로 내린다.
        """
        import cv2

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
        self.prune()
        return path

    def _sorted_images(self) -> list[Path]:
        """오래된 것부터. 파일명이 `%Y%m%d_%H%M%S_%f` 기반이라 **이름 정렬이
        곧 시간 정렬**이다(`_timestamp_name`). mtime 을 보지 않는 이유는 파일
        복사·이동이 mtime 을 바꿔도 증빙의 시각은 이름에 남아 있기 때문이다.
        """
        return sorted(self.events_dir.glob("*.jpg"))

    def prune(self) -> int:
        """상한을 넘는 만큼 오래된 것부터 지운다. 지운 개수를 반환한다."""
        if self.max_bytes is None and self.max_files is None:
            return 0

        try:
            paths = self._sorted_images()
        except OSError as exc:
            print(f"[storage] 이벤트 디렉터리 조회 실패: {exc}", file=sys.stderr)
            return 0

        sizes: list[int] = []
        for path in paths:
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                sizes.append(0)  # 이미 사라진 파일은 0으로 두고 삭제 대상에 남긴다
        total = sum(sizes)

        removed = 0
        freed = 0
        # 가장 오래된 것부터 훑으며 두 상한 **모두** 만족할 때까지 지운다.
        # 마지막 한 장은 남긴다 — 상한이 장당 크기보다 작게 설정된 경우에
        # 방금 저장한 증빙까지 지우면 저장 자체가 무의미해진다.
        for path, size in zip(paths, sizes):
            over_bytes = self.max_bytes is not None and total > self.max_bytes
            over_files = (
                self.max_files is not None and len(paths) - removed > self.max_files
            )
            if not (over_bytes or over_files):
                break
            if len(paths) - removed <= 1:
                break
            try:
                path.unlink()
            except OSError as exc:
                print(f"[storage] 이벤트 이미지 삭제 실패: {path} ({exc})",
                      file=sys.stderr)
                continue
            removed += 1
            freed += size
            total -= size

        if removed:
            # 조용히 지우면 나중에 "왜 그 시각 증빙이 없나" 에 답할 수 없다.
            # 이건 캐시가 아니라 증빙 데이터다.
            print(
                f"[storage] 보존 상한 초과로 오래된 이벤트 이미지 {removed}장"
                f"({freed / 1024 / 1024:.1f}MB)을 지웠다 — 남은 {len(paths) - removed}장"
                f"({total / 1024 / 1024:.1f}MB), 상한 "
                f"bytes={self.max_bytes} files={self.max_files}",
                file=sys.stderr,
            )
        return removed

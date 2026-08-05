"""이벤트 증빙 이미지 보존 상한 검증 (S15P11A301-265).

cv2 를 쓰지 않는다 — 상한 판정은 파일 크기와 이름만 보므로, 실제 JPEG 대신
크기를 지정한 더미 파일로 확인한다. 그래야 검증이 인코더 성능·화질 설정에
흔들리지 않는다.

실행:
    python -m pytest tests -q
    python tests/test_event_image_retention.py     (pytest 없이도 동작)
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import EventImageStore  # noqa: E402


class _Workspace:
    """`tmp_path` fixture 대신 쓰는 임시 디렉터리. pytest 없이도 돈다."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        return Path(self._tmp.name)

    def __exit__(self, *_exc) -> None:
        self._tmp.cleanup()


def _make(dir_path: Path, name: str, size: int) -> Path:
    """`name` 이 파일명 정렬(=시간 정렬)을 결정한다."""
    path = dir_path / f"{name}.jpg"
    path.write_bytes(b"\x00" * size)
    return path


def _names(dir_path: Path) -> list[str]:
    return sorted(p.stem for p in dir_path.glob("*.jpg"))


@contextlib.contextmanager
def _captured_stderr():
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        yield buffer


# ── 상한이 없으면 종전 동작이다 ──────────────────────────────────────────


def test_상한이_없으면_아무것도_지우지_않는다() -> None:
    with _Workspace() as work:
        for i in range(10):
            _make(work, f"2026{i:04d}", 1000)

        store = EventImageStore(work)

        assert store.prune() == 0
        assert len(_names(work)) == 10


# ── 바이트 상한 ─────────────────────────────────────────────────────────


def test_바이트_상한을_넘으면_오래된_것부터_지운다() -> None:
    with _Workspace() as work:
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)

        # 250 이하가 되려면 앞의 셋을 지워야 한다(남는 둘 = 200).
        EventImageStore(work, max_bytes=250)

        assert _names(work) == ["20260104", "20260105"]


def test_상한_이내면_지우지_않는다() -> None:
    with _Workspace() as work:
        for i in range(1, 4):
            _make(work, f"2026010{i}", 100)

        EventImageStore(work, max_bytes=1000)

        assert len(_names(work)) == 3


def test_경계값에서는_지우지_않는다() -> None:
    """총량이 상한과 **같으면** 초과가 아니다."""
    with _Workspace() as work:
        for i in range(1, 4):
            _make(work, f"2026010{i}", 100)

        EventImageStore(work, max_bytes=300)

        assert len(_names(work)) == 3


def test_남기는_것은_가장_최근_것들이다() -> None:
    """파일명이 시간순이므로 이름 정렬로 판단한다."""
    with _Workspace() as work:
        for name in ["20260101_000001", "20260101_000002",
                     "20260101_000003", "20260102_000001"]:
            _make(work, name, 100)

        EventImageStore(work, max_bytes=150)

        assert _names(work) == ["20260102_000001"]


# ── 개수 상한 ───────────────────────────────────────────────────────────


def test_개수_상한을_넘으면_오래된_것부터_지운다() -> None:
    with _Workspace() as work:
        for i in range(1, 8):
            _make(work, f"2026010{i}", 10)

        EventImageStore(work, max_files=3)

        assert _names(work) == ["20260105", "20260106", "20260107"]


def test_두_상한을_모두_만족시킨다() -> None:
    """어느 쪽이든 넘으면 지운다."""
    with _Workspace() as work:
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)

        # 바이트로는 넷까지 되지만 개수 상한이 2 다.
        EventImageStore(work, max_bytes=400, max_files=2)

        assert _names(work) == ["20260104", "20260105"]


# ── 기동 시 정리 ────────────────────────────────────────────────────────


def test_기동_시_기존_초과분을_정리한다() -> None:
    """save() 안에서만 검사하면 이전 run 이 남긴 초과분이 그대로 남는다."""
    with _Workspace() as work:
        for i in range(1, 11):
            _make(work, f"202601{i:02d}", 100)

        EventImageStore(work, max_bytes=300)

        assert len(_names(work)) == 3


def test_디렉터리가_없으면_만든다() -> None:
    with _Workspace() as work:
        target = work / "events"

        EventImageStore(target, max_bytes=1000)

        assert target.is_dir()


def test_빈_디렉터리에서_터지지_않는다() -> None:
    with _Workspace() as work:
        store = EventImageStore(work, max_bytes=1, max_files=1)

        assert store.prune() == 0


# ── 마지막 한 장은 지킨다 ───────────────────────────────────────────────


def test_상한이_장당_크기보다_작아도_한_장은_남긴다() -> None:
    """방금 저장한 증빙까지 지우면 저장 자체가 무의미해진다."""
    with _Workspace() as work:
        _make(work, "20260101", 5000)
        _make(work, "20260102", 5000)

        EventImageStore(work, max_bytes=100)

        assert _names(work) == ["20260102"], "가장 최근 한 장은 남아야 한다"


def test_개수_상한_0_이어도_한_장은_남긴다() -> None:
    with _Workspace() as work:
        _make(work, "20260101", 10)

        EventImageStore(work, max_files=0)

        assert len(_names(work)) == 1


# ── 삭제를 보고한다 ─────────────────────────────────────────────────────


def test_삭제를_로그로_남긴다() -> None:
    """조용히 지우면 나중에 '왜 그 시각 증빙이 없나' 에 답할 수 없다."""
    with _Workspace() as work:
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)

        with _captured_stderr() as err:
            EventImageStore(work, max_bytes=250)

        message = err.getvalue()
        assert "3장" in message, f"지운 개수가 안 남았다: {message}"
        assert "상한" in message
        assert "남은 2장" in message


def test_지우지_않으면_로그가_없다() -> None:
    with _Workspace() as work:
        _make(work, "20260101", 10)

        with _captured_stderr() as err:
            EventImageStore(work, max_bytes=1000)

        assert err.getvalue() == ""


def test_지운_개수를_반환한다() -> None:
    with _Workspace() as work:
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)
        store = EventImageStore(work)
        store.max_bytes = 250

        with _captured_stderr():
            assert store.prune() == 3


# ── jpg 만 건드린다 ─────────────────────────────────────────────────────


def test_jpg_가_아닌_파일은_건드리지_않는다() -> None:
    """같은 디렉터리에 다른 산출물이 놓일 수 있다.

    이름이 **정렬상 가장 앞**이어야 의미가 있다. 뒤에 두면 「마지막 한 장은
    남긴다」 규칙에 걸려 우연히 살아남고, glob 이 `*` 로 바뀌어도 이 검증이
    통과해 버린다(뮤테이션 시험에서 실제로 놓쳤다).
    """
    with _Workspace() as work:
        keep = work / "00_notes.txt"
        keep.write_text("지우지 말 것")
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)

        with _captured_stderr():
            EventImageStore(work, max_bytes=100)

        assert keep.exists(), "jpg 가 아닌 파일을 지웠다"


def test_삭제_실패는_삼키고_계속한다() -> None:
    """한 장이 이미 사라져도 나머지 정리가 멈추면 안 된다."""
    with _Workspace() as work:
        for i in range(1, 6):
            _make(work, f"2026010{i}", 100)

        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self: Path, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("사라짐")
            return real_unlink(self, *args, **kwargs)

        Path.unlink = flaky_unlink  # type: ignore[method-assign]
        try:
            with _captured_stderr() as err:
                EventImageStore(work, max_bytes=250)
        finally:
            Path.unlink = real_unlink  # type: ignore[method-assign]

        assert calls["n"] > 1, "첫 실패에서 멈췄다"
        assert "삭제 실패" in err.getvalue()


if __name__ == "__main__":
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"{name}: OK")

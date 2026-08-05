"""대화 세션의 관찰 원본을 파일로 남긴다 (S15P11A301-178).

시나리오 C가 3차까지 재현되지 않았는데, 마이크에 무엇이 들어왔는지 사후에 확인할
방법이 없어 코드 결함인지 주변 잡음인지 판정하지 못했다. 이 모듈이 그 계측을 담당한다.

  sessions/<timestamp>/
    session.jsonl              세션 시작 · 턴별 관찰 · 세션 종료
    report.json                33-6 보고값과 위험도
    turn_01_INTRO_a1.wav       청취 원본 (정규화 전)

**정규화 전 원본만 저장한다.** 정규화는 `NORM_TARGET_RMS`를 목표로 음량을 끌어올리므로,
정규화 후 파일만 남기면 "큰 목소리가 들어왔다"와 "작은 에코가 증폭됐다"를 구분할 수
없다. 정규화 결과는 원본의 결정적 함수이므로 필요하면 언제든 재생성할 수 있고,
`session.jsonl`의 세션 시작 줄에 그때 쓰인 목표값을 함께 남긴다.

개인정보 제약
  - **기본 비활성.** `SENTINEL_SESSION_LOG_DIR`가 주어질 때만 저장한다. 현장 로봇이
    요구조자 음성을 기본으로 쌓으면 안 된다.
  - 저장 위치를 저장소 안에 두더라도 커밋되지 않도록 `.gitignore`에 등록해 두었다.
  - 보관 기간과 삭제 책임은 docs/08-AI-음성.md 33.5에 적는다.

이 모듈의 실패는 대화 세션을 중단시키지 않는다. 계측이 임무를 멈추면 안 된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config

ENV_DIR = "SENTINEL_SESSION_LOG_DIR"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SessionLog:
    """세션 하나의 저장 대상. ``directory``가 None이면 아무 것도 쓰지 않는다."""

    directory: Path | None = None
    on_event: Callable[[str], None] = lambda message: None

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    # ── 기록 ──────────────────────────────────────────────────────
    def start(self, *, source: str, timeout_seconds: float) -> None:
        self._append(
            {
                "type": "session_start",
                "at": _utc_now().isoformat(timespec="milliseconds"),
                "source": source,
                # 정규화 결과를 재생성하고 무음 판정을 재현하는 데 필요한 값들이다.
                "sampleRate": config.FS,
                "silenceRms": config.SILENCE_RMS,
                "normTargetRms": config.NORM_TARGET_RMS,
                "sttBackend": "remote",
                "sttModel": config.ASR_MODEL_LABEL,
                "llmModel": config.LLM_MODEL,
                "device": "remote-gpu",
                "timeoutSeconds": timeout_seconds,
            }
        )

    def audio(self, question: str, attempt: int, index: int, wav: Any) -> str | None:
        """청취 원본을 PCM 16-bit로 저장하고 파일명을 돌려준다."""
        if not self.enabled or wav is None:
            return None
        filename = f"turn_{index:02d}_{question}_a{attempt}.wav"
        try:
            import soundfile

            soundfile.write(
                str(self.directory / filename), wav, config.FS, subtype="PCM_16"
            )
        except Exception as error:
            self._warn(f"청취 원본 저장 실패 {filename}: {type(error).__name__}")
            return None
        return filename

    def turn(self, record: dict[str, Any]) -> None:
        self._append({"type": "turn", **record})

    def finish(self, record: dict[str, Any]) -> None:
        self._append(
            {
                "type": "session_end",
                "at": _utc_now().isoformat(timespec="milliseconds"),
                **record,
            }
        )

    def announcement(self, guide: str, status: str, detail: str = "") -> None:
        """세션 종료 안내의 재생 결과. 상태머신 밖에서 일어나므로 따로 남긴다.

        어떤 문구를 말했는지가 시연·현장 검증의 근거다. 발신 상태에 따라 문구가
        달라지므로 실제로 무엇이 나갔는지 기록해야 대조할 수 있다.
        """
        self._append(
            {
                "type": "announcement",
                "at": _utc_now().isoformat(timespec="milliseconds"),
                "guide": guide,
                "status": status,
                "detail": detail,
            }
        )

    def report(self, payload: dict[str, Any]) -> None:
        """33-6 보고값을 별도 파일로 남긴다. 관제로 나간 것과 같은 내용이다."""
        if not self.enabled:
            return
        try:
            path = self.directory / "report.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as error:
            self._warn(f"보고값 저장 실패: {type(error).__name__}")

    # ── 내부 ──────────────────────────────────────────────────────
    def _append(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            with (self.directory / "session.jsonl").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as error:
            self._warn(f"세션 기록 저장 실패: {type(error).__name__}")

    def _warn(self, message: str) -> None:
        try:
            self.on_event(f"[LOG] {message}")
        except Exception:
            pass


def open_session_log(
    root: str | os.PathLike[str] | None = None,
    *,
    on_event: Callable[[str], None] = lambda message: None,
) -> SessionLog:
    """저장 위치가 지정된 경우에만 세션 디렉터리를 만든다.

    지정이 없으면 비활성 로그를 돌려준다. 호출부가 분기하지 않아도 되도록
    None이 아니라 비활성 객체를 준다.
    """
    target = root if root is not None else os.getenv(ENV_DIR)
    if not target:
        return SessionLog(None, on_event)

    stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
    directory = Path(target).expanduser() / stamp
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception as error:
        # 저장을 못 해도 세션은 진행한다. 계측이 임무를 멈추면 안 된다.
        log = SessionLog(None, on_event)
        log._warn(f"세션 디렉터리 생성 실패 {directory}: {type(error).__name__}")
        return log

    log = SessionLog(directory, on_event)
    log._warn(f"세션 기록 위치 {directory}")
    return log

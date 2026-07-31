"""ConversationMachine에 실제 마이크·STT·GMS·안내 음성을 연결한다.

S15P11A301-112는 대화 순서와 실패 규칙만 담당하는 `ConversationMachine`을 만들고
입출력 세 개(`prompt`·`listen`·`interpret`)를 주입받도록 두었다. 이 모듈이 그 자리에
실물을 꽂는 어댑터다. 하드웨어와 네트워크는 `SessionDependencies`로 주입하므로
단위 테스트는 장비 없이 같은 경로를 검증할 수 있다.

  prompt    → GuidePlayer.play_text()  승인된 사전녹음 WAV만 재생
  listen    → 마이크 녹음 → 무음 판정 → 정규화 → VAD → STT → 환각 가드
  interpret → extract_with_status()    GMS 호출, 실패 시 33-8 키워드 폴백

설계 원칙
  - 질문 하나가 채우는 필드는 하나다. 개방형 발화에서 여러 필드를 동시에 채우는
    적응형 흐름은 S15P11A301-148 범위이며 이 모듈은 배선만 책임진다.
  - STT가 실패한 경우를 무응답으로 기록하지 않는다. 관찰 사실 그대로
    `VOICE_DETECTED_STT_FAILED`로 분류되도록 `stt_text`만 비운다(명세 33-3).
  - 문구를 생성하지 않는다. 승인 목록에 없는 문장은 재생 자체가 거부된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from . import config
from .audio import normalize, rms
from .conversation import (
    SESSION_TIMEOUT_SECONDS,
    AudioObservation,
    ConversationMachine,
    QuestionCode,
    SessionResult,
    SessionState,
)
from .guide_audio import GuidePlayer, PlaybackResult
from .safety import is_valid_stt

# 질문이 채우는 33-6 추출 필드. INTRO는 발화 존재 자체가 답이므로 제외한다.
EXTRACTION_FIELD_BY_QUESTION: dict[QuestionCode, str] = {
    QuestionCode.COUNT: "reportedResponsiveCount",
    QuestionCode.MOBILITY: "mobilityStatus",
    QuestionCode.URGENT: "urgentConditionReported",
}

# 질문별 청취 시간(초). 자유 서술을 요구하는 질문은 길게 둔다.
LISTEN_SECONDS: dict[QuestionCode, float] = {
    QuestionCode.INTRO: 5.0,
    QuestionCode.COUNT: 6.0,
    QuestionCode.MOBILITY: 6.0,
    QuestionCode.URGENT: 8.0,
}

# 해석값으로 인정하지 않는 값. UNKNOWN은 "답은 했으나 확정 못 함"이므로
# None을 돌려 RESPONSE_UNRECOGNIZED로 분류시키고 관제 확인 대상으로 남긴다.
_UNDETERMINED = {None, "UNKNOWN", ""}


@dataclass
class SessionDependencies:
    """하드웨어·네트워크 의존성. 테스트에서는 전부 대체한다."""

    record: Callable[[float], np.ndarray]
    """청취 시간(초)을 받아 16kHz mono float32 오디오를 반환한다."""

    has_speech: Callable[[np.ndarray], bool]
    """VAD 판정."""

    transcribe: Callable[[np.ndarray], tuple[str, float]]
    """오디오를 (텍스트, no_speech_prob)로 변환한다."""

    extract: Callable[[str], Any]
    """발화를 33-6 추출값으로 구조화한다. `GmsCallResult`를 반환한다."""

    player: GuidePlayer
    """승인된 안내 음성 재생기."""


@dataclass
class TurnDiagnostics:
    """세션 후 로깅·보고 판단에 쓰는 부수 관찰값."""

    question: QuestionCode
    raw_rms: float = 0.0
    stt_text: str | None = None
    no_speech_prob: float | None = None
    stt_invalid_reason: str | None = None
    extraction_source: str | None = None
    playback: PlaybackResult | None = None


class VoiceSessionRunner:
    """실물 입출력을 연결한 다턴 대화 세션 실행기."""

    def __init__(
        self,
        deps: SessionDependencies,
        *,
        abort_requested: Callable[[], SessionState | None] = lambda: None,
        timeout_seconds: float = SESSION_TIMEOUT_SECONDS,
        listen_seconds: dict[QuestionCode, float] | None = None,
        on_event: Callable[[str], None] = lambda message: None,
    ):
        self.deps = deps
        self.abort_requested = abort_requested
        self.timeout_seconds = timeout_seconds
        self.listen_seconds = listen_seconds or LISTEN_SECONDS
        self._on_event = on_event
        self.diagnostics: list[TurnDiagnostics] = []
        self._extractions: list[dict[str, Any]] = []

    def on_event(self, message: str) -> None:
        """진행 로그. 출력 실패가 대화 세션을 중단시키면 안 된다.

        콘솔 인코딩(cp949 등)이나 리다이렉션 문제로 쓰기가 실패해도 세션은
        계속 진행한다. 메시지는 ASCII 태그로 두어 인코딩 의존을 줄인다.
        """
        try:
            self._on_event(message)
        except Exception:
            pass

    # ── 주입 대상 세 개 ────────────────────────────────────────────
    def prompt(self, question: QuestionCode, text: str) -> None:
        """승인된 문구만 재생한다. 실패는 세션을 중단시키지 않고 기록만 한다."""
        result = self.deps.player.play_text(text)
        self._diagnostic(question).playback = result
        if result.ok:
            self.on_event(f"[PLAY] {question.value}: {text}")
        else:
            # 자산 누락·형식 오류는 임의 TTS로 대체하지 않는다(승인 문구 원칙).
            self.on_event(
                f"[WARN] {question.value}: 안내 음성 재생 실패 "
                f"{result.status.value} ({result.detail})"
            )

    def listen(self, question: QuestionCode, attempt: int) -> AudioObservation:
        """녹음→무음판정→정규화→VAD→STT→환각가드를 한 관찰값으로 만든다."""
        diagnostic = self._diagnostic(question)
        seconds = self.listen_seconds.get(question, 6.0)
        try:
            wav = self.deps.record(seconds)
        except Exception as exc:  # 마이크 분리·점유 등 장치 오류
            self.on_event(f"[FAIL] {question.value}: 오디오 장치 오류 {exc}")
            return AudioObservation(False, None, audio_error=True)

        raw_rms = rms(wav)
        diagnostic.raw_rms = raw_rms
        if raw_rms < config.SILENCE_RMS:
            self.on_event(f"[NOVOICE] {question.value}: 무음 rms={raw_rms:.4f}")
            return AudioObservation(False)

        normalized = normalize(wav)
        if not self.deps.has_speech(normalized):
            self.on_event(f"[NOVOICE] {question.value}: VAD 음성 미검출")
            return AudioObservation(False)

        text, no_speech_prob = self.deps.transcribe(normalized)
        diagnostic.stt_text = text
        diagnostic.no_speech_prob = no_speech_prob

        valid, reason = is_valid_stt(text, no_speech_prob, config.STT_PROMPT)
        if not valid:
            # 음성은 있었다. STT만 실패했으므로 무응답으로 기록하지 않는다.
            diagnostic.stt_invalid_reason = reason
            self.on_event(f"[STTFAIL] {question.value}: STT 무효 {reason}")
            return AudioObservation(True, None)

        self.on_event(f"[STT] {question.value}: {text}")
        return AudioObservation(True, text)

    def interpret(self, question: QuestionCode, text: str) -> Any | None:
        """질문이 요구한 필드값만 돌려준다. 확정 못 하면 None."""
        if question == QuestionCode.INTRO:
            # 발화가 있었다는 사실이 곧 응답이다. 내용 해석에 의존하지 않는다.
            return True

        field_name = EXTRACTION_FIELD_BY_QUESTION.get(question)
        if field_name is None:
            return None

        result = self.deps.extract(text)
        extraction = getattr(result, "extraction", result) or {}
        source = getattr(result, "source", None)
        self._diagnostic(question).extraction_source = source
        self._extractions.append(dict(extraction))
        if source and source != "GMS":
            self.on_event(f"[LLM] {question.value}: 추출 경로 {source}")

        value = extraction.get(field_name)
        return None if value in _UNDETERMINED else value

    # ── 실행 ──────────────────────────────────────────────────────
    def run(self) -> SessionResult:
        machine = ConversationMachine(
            prompt=self.prompt,
            listen=self.listen,
            interpret=self.interpret,
            abort_requested=self.abort_requested,
            timeout_seconds=self.timeout_seconds,
        )
        return machine.run()

    @property
    def used_fallback(self) -> bool:
        """세션 중 한 번이라도 33-8 키워드 폴백을 썼는지."""
        return any(
            diagnostic.extraction_source not in (None, "GMS")
            for diagnostic in self.diagnostics
        )

    def _diagnostic(self, question: QuestionCode) -> TurnDiagnostics:
        for diagnostic in self.diagnostics:
            if diagnostic.question == question:
                return diagnostic
        diagnostic = TurnDiagnostics(question=question)
        self.diagnostics.append(diagnostic)
        return diagnostic

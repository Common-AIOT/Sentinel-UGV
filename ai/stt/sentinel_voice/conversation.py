"""요구조자 다턴 대화 상태머신과 응답 4분류."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Callable

from .safety import report_defaults


class QuestionCode(str, Enum):
    INTRO = "INTRO"
    COUNT = "COUNT"
    MOBILITY = "MOBILITY"
    URGENT = "URGENT"
    CLOSING = "CLOSING"


class SessionState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    PROMPTING = "PROMPTING"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    LLM_INTERPRETING = "LLM_INTERPRETING"
    TTS_RESPONDING = "TTS_RESPONDING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    ABORTED_MANUAL = "ABORTED_MANUAL"
    ABORTED_SAFETY = "ABORTED_SAFETY"
    FAILED_AUDIO = "FAILED_AUDIO"


class ResponseClass(str, Enum):
    NO_VOICE_DETECTED = "NO_VOICE_DETECTED"
    VOICE_DETECTED_STT_FAILED = "VOICE_DETECTED_STT_FAILED"
    RESPONSE_UNRECOGNIZED = "RESPONSE_UNRECOGNIZED"
    ANSWER_STRUCTURED = "ANSWER_STRUCTURED"


PROMPTS = {
    QuestionCode.INTRO: "탐사 로봇입니다. 대답할 수 있는 분은 말씀해 주세요.",
    QuestionCode.COUNT: "현재 응답 가능한 인원 수를 숫자로 말씀해 주세요.",
    QuestionCode.MOBILITY: "스스로 이동할 수 있습니까? 예 또는 아니오로 답해 주세요.",
    QuestionCode.URGENT: "심한 출혈이나 호흡 곤란이 있습니까?",
    QuestionCode.CLOSING: "정보를 관제실에 전달했습니다. 구조 요청을 기다려 주세요.",
}

FIELD_BY_QUESTION = {
    QuestionCode.INTRO: "anyResponseDetected",
    QuestionCode.COUNT: "reportedResponsiveCount",
    QuestionCode.MOBILITY: "mobilityStatus",
    QuestionCode.URGENT: "urgentConditionReported",
}


@dataclass(frozen=True)
class AudioObservation:
    voice_detected: bool
    stt_text: str | None = None
    audio_error: bool = False


@dataclass
class TurnResult:
    question: QuestionCode
    response_class: ResponseClass
    stt_text: str | None
    value: Any = "UNKNOWN"
    operator_review_required: bool = False
    attempt: int = 1


@dataclass
class SessionResult:
    state: SessionState = SessionState.NOT_STARTED
    fields: dict[str, Any] = field(default_factory=report_defaults)
    turns: list[TurnResult] = field(default_factory=list)
    state_log: list[SessionState] = field(
        default_factory=lambda: [SessionState.NOT_STARTED]
    )
    operator_review_required: bool = False
    termination_reason: str | None = None


def classify_response(
    observation: AudioObservation, structured_value: Any | None
) -> tuple[ResponseClass, bool]:
    """VAD·STT·구조화 결과를 명세 33-3의 네 상태로 분류한다."""
    if not observation.voice_detected:
        return ResponseClass.NO_VOICE_DETECTED, False
    if not (observation.stt_text or "").strip():
        return ResponseClass.VOICE_DETECTED_STT_FAILED, True
    if structured_value is None:
        return ResponseClass.RESPONSE_UNRECOGNIZED, True
    return ResponseClass.ANSWER_STRUCTURED, False


class ConversationMachine:
    """입출력 함수를 주입받아 명세 순서와 실패 규칙만 책임진다."""

    def __init__(
        self,
        prompt: Callable[[QuestionCode, str], None],
        listen: Callable[[QuestionCode, int], AudioObservation],
        interpret: Callable[[QuestionCode, str], Any | None],
        abort_requested: Callable[[], SessionState | None] = lambda: None,
        timeout_seconds: float = 120,
        clock: Callable[[], float] = monotonic,
    ):
        self.prompt = prompt
        self.listen = listen
        self.interpret = interpret
        self.abort_requested = abort_requested
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    @staticmethod
    def _transition(result: SessionResult, state: SessionState) -> None:
        result.state = state
        result.state_log.append(state)

    def _stop_if_needed(
        self, result: SessionResult, started_at: float
    ) -> bool:
        abort_state = self.abort_requested()
        if abort_state in {
            SessionState.ABORTED_MANUAL,
            SessionState.ABORTED_SAFETY,
        }:
            result.termination_reason = abort_state.value
            result.fields["terminationReason"] = abort_state.value
            self._transition(result, abort_state)
            return True
        if self.clock() - started_at >= self.timeout_seconds:
            result.termination_reason = "TIMEOUT"
            result.fields["terminationReason"] = "TIMEOUT"
            self._transition(result, SessionState.COMPLETED)
            return True
        return False

    def run(self) -> SessionResult:
        result = SessionResult()
        started_at = self.clock()

        for question in QuestionCode:
            if self._stop_if_needed(result, started_at):
                return result

            self._transition(result, SessionState.PROMPTING)
            self.prompt(question, PROMPTS[question])
            self._transition(result, SessionState.TTS_RESPONDING)

            if question == QuestionCode.CLOSING:
                result.termination_reason = result.termination_reason or "NORMAL"
                result.fields["terminationReason"] = result.termination_reason
                self._transition(result, SessionState.COMPLETED)
                return result

            max_attempts = 2 if question == QuestionCode.INTRO else 1
            for attempt in range(1, max_attempts + 1):
                if self._stop_if_needed(result, started_at):
                    return result

                self._transition(result, SessionState.LISTENING)
                observation = self.listen(question, attempt)
                if observation.audio_error:
                    result.termination_reason = "AUDIO_DEVICE_ERROR"
                    result.fields["terminationReason"] = "AUDIO_DEVICE_ERROR"
                    self._transition(result, SessionState.FAILED_AUDIO)
                    return result

                structured_value = None
                if observation.voice_detected:
                    self._transition(result, SessionState.TRANSCRIBING)
                    if (observation.stt_text or "").strip():
                        self._transition(result, SessionState.LLM_INTERPRETING)
                        structured_value = self.interpret(
                            question, observation.stt_text or ""
                        )

                response_class, review_required = classify_response(
                    observation, structured_value
                )
                turn = TurnResult(
                    question=question,
                    response_class=response_class,
                    stt_text=observation.stt_text,
                    value=(
                        structured_value
                        if response_class == ResponseClass.ANSWER_STRUCTURED
                        else "UNKNOWN"
                    ),
                    operator_review_required=review_required,
                    attempt=attempt,
                )
                result.turns.append(turn)
                result.operator_review_required |= review_required
                result.fields["operatorReviewRequired"] = (
                    result.operator_review_required
                )

                if (
                    question == QuestionCode.INTRO
                    and response_class == ResponseClass.NO_VOICE_DETECTED
                    and attempt < max_attempts
                ):
                    self._transition(result, SessionState.RETRYING)
                    continue

                field_name = FIELD_BY_QUESTION[question]
                if question == QuestionCode.INTRO:
                    result.fields[field_name] = (
                        response_class != ResponseClass.NO_VOICE_DETECTED
                    )
                else:
                    result.fields[field_name] = turn.value
                    if (
                        question == QuestionCode.COUNT
                        and isinstance(turn.value, int)
                        and not isinstance(turn.value, bool)
                    ):
                        result.fields[
                            "reportedCountStatus"
                        ] = "SELF_REPORTED_GROUP_COUNT"
                break

        return result

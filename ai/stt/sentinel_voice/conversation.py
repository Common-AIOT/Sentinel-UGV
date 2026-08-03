"""요구조자 다턴 대화 상태머신과 응답 4분류."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Callable

from .guide_audio import GUIDE_ASSETS, GuideCode
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


# 상태머신이 재생하는 문구. CLOSING은 여기 없다.
#
# 종료 안내는 보고 발신 상태에 따라 문구가 달라지는데, 상태머신이 도는 시점에는
# 아직 발신하지 않았으므로 그 상태를 알 수 없다. 이전에는 여기에 진행형 문구를
# 박아 두고 발신 후에 실제 상태로 한 번 더 안내해서, 두 문구가 같아지는 경로에서
# 같은 말이 두 번 나왔다. 종료 안내는 상태를 아는 전송 단계가 한 번만 한다.
PROMPTS = {
    QuestionCode.INTRO: GUIDE_ASSETS[GuideCode.INTRO].text,
    QuestionCode.COUNT: GUIDE_ASSETS[GuideCode.ASK_COUNT].text,
    QuestionCode.MOBILITY: GUIDE_ASSETS[GuideCode.ASK_MOBILITY].text,
    QuestionCode.URGENT: GUIDE_ASSETS[GuideCode.ASK_URGENT].text,
}

# 청취가 있는 질문의 진행 순서. CLOSING은 안내만 하고 답을 받지 않는다.
#
# URGENT를 먼저 묻는다. 세션이 조기 종료(타임아웃·중단)되어도 가장 중요한
# 부상 정보부터 확보한다(S15P11A301-146 v2).
ASKED_QUESTIONS = (
    QuestionCode.INTRO,
    QuestionCode.URGENT,
    QuestionCode.MOBILITY,
    QuestionCode.COUNT,
)

# 세션 전체 예산(초). 기본값은 여기 한 곳에만 둔다 — 두 곳에 복제했더니
# 실기 경로에 반영되지 않은 사고가 있었다. 산정 근거는 docs/README.md 11-2.
SESSION_TIMEOUT_SECONDS = 180.0

FIELD_BY_QUESTION = {
    QuestionCode.INTRO: "anyResponseDetected",
    QuestionCode.COUNT: "reportedResponsiveCount",
    QuestionCode.MOBILITY: "mobilityStatus",
    QuestionCode.URGENT: "urgentConditionReported",
}

# 질문별 재질문 정책: (다시 물을 응답 분류, 재질문에 쓸 안내).
#
# 재질문은 INTRO 무응답 1회뿐이다. 들었으나 값을 확정하지 못한 응답(STT 실패,
# 값 미확정)은 되묻지 않는다 — 급박한 상황에 다시 말해 달라는 요구가 이질적이라는
# 컨설팅 지적으로 제거했다(S15P11A301-201). 확정 실패는 UNKNOWN으로 두며,
# 원문 전사·녹음이 세션 기록에 남아 관제가 직접 판단한다(S15P11A301-202).
RETRY_POLICY = {
    QuestionCode.INTRO: (
        frozenset({ResponseClass.NO_VOICE_DETECTED}),
        GuideCode.RETRY_NO_RESPONSE,
    ),
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
        timeout_seconds: float = SESSION_TIMEOUT_SECONDS,
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

        # 질문 순서를 목록으로 들고 간다. 무응답이 확정되면 남은 질문을 버린다(§11-4).
        remaining = list(ASKED_QUESTIONS)
        while remaining:
            question = remaining.pop(0)
            if self._stop_if_needed(result, started_at):
                return result

            self._transition(result, SessionState.PROMPTING)
            self.prompt(question, PROMPTS[question])
            self._transition(result, SessionState.TTS_RESPONDING)

            retry_classes, retry_guide = RETRY_POLICY.get(
                question, (frozenset(), None)
            )
            max_attempts = 2
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

                if response_class in retry_classes and attempt < max_attempts:
                    # 재질문 문구는 질문을 반복하지 않는다. 요구조자는 무엇을
                    # 물었는지 알고 있고, 승인된 문구가 그렇게 되어 있다.
                    self._transition(result, SessionState.RETRYING)
                    self.prompt(question, GUIDE_ASSETS[retry_guide].text)
                    continue

                field_name = FIELD_BY_QUESTION[question]
                if question == QuestionCode.INTRO:
                    answered = (
                        response_class != ResponseClass.NO_VOICE_DETECTED
                    )
                    result.fields[field_name] = answered
                    if not answered:
                        # 재질문까지 반응이 없으면 남은 질문을 하지 않는다. 반응 없는
                        # 요구조자 앞에서 30초를 더 쓰는 대신 관제에 즉시 알린다.
                        # 종료 안내는 전송 단계가 하므로 여기서 끊어도 요구조자는
                        # 마지막 안내를 듣는다.
                        #
                        # `VOICE_DETECTED_STT_FAILED`는 여기 해당하지 않는다. 사람을
                        # 들었으므로 남은 질문을 계속한다(명세 33-3).
                        remaining = []
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

        # 질문이 끝났다. 종료 안내는 발신 상태를 아는 호출자가 재생한다.
        result.termination_reason = result.termination_reason or "NORMAL"
        result.fields["terminationReason"] = result.termination_reason
        self._transition(result, SessionState.COMPLETED)
        return result

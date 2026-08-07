"""비전 Encounter 계약과 음성 세션 시작 조건을 관리한다.

ROS 2 의존성을 두지 않는다. 실제 ``/perception/encounter`` 구독 노드는
문자열 메시지를 :func:`parse_encounter_message`로 변환하고
:class:`EncounterSessionCoordinator`에 전달하는 얇은 어댑터가 된다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
UTC_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)
ALLOWED_FIELDS = {
    "encounterId",
    "phase",
    "detectedAt",
    "personCount",
    "trackIds",
    "confidence",
    "pose",
    "missionId",
}
REQUIRED_FIELDS = {
    "encounterId",
    "phase",
    "detectedAt",
    "personCount",
}


class EncounterContractError(ValueError):
    """Encounter JSON이 공통 계약을 만족하지 않을 때 발생한다."""


class EncounterPhase(str, Enum):
    """Mission Manager가 발행하는 Encounter 진행 단계."""

    CONFIRMED = "CONFIRMED"
    APPROACHED = "APPROACHED"
    ENDED = "ENDED"
    REDETECTED = "REDETECTED"
    LOST = "LOST"


@dataclass(frozen=True)
class EncounterContext:
    """음성 세션과 후속 보고에 보존할 비전 Encounter 문맥."""

    encounter_id: str
    phase: EncounterPhase
    detected_at: str
    person_count: int
    mission_id: str | None = None
    track_ids: tuple[int, ...] | None = None
    confidence: float | None = None
    pose: dict[str, Any] | None = None

    def report_context(self) -> dict[str, Any]:
        """33-6 음성 필드와 분리해서 최종 보고 Envelope에 넣을 문맥."""

        return {
            "encounterId": self.encounter_id,
            "missionId": self.mission_id,
            "visionPersonCount": self.person_count,
        }


def _require_uuid(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not UUID_PATTERN.fullmatch(value):
        raise EncounterContractError(f"{field}: 소문자 UUID 형식이 아닙니다")
    return value


def _optional_fields(data: dict[str, Any]) -> tuple[
    tuple[int, ...] | None,
    float | None,
    dict[str, Any] | None,
]:
    track_ids = data.get("trackIds")
    if track_ids is not None:
        if not isinstance(track_ids, list) or any(
            isinstance(item, bool) or not isinstance(item, int)
            for item in track_ids
        ):
            raise EncounterContractError("trackIds: 정수 배열 또는 null이어야 합니다")
        track_ids = tuple(track_ids)

    confidence = data.get("confidence")
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise EncounterContractError("confidence: 0~1 숫자 또는 null이어야 합니다")
        confidence = float(confidence)

    pose = data.get("pose")
    if pose is not None:
        if not isinstance(pose, dict) or set(pose) != {"x", "y", "yaw", "mapId"}:
            raise EncounterContractError("pose: x, y, yaw, mapId만 가져야 합니다")
        for field in ("x", "y", "yaw"):
            value = pose[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EncounterContractError(f"pose.{field}: 숫자여야 합니다")
        if pose["mapId"] is not None and not isinstance(pose["mapId"], str):
            raise EncounterContractError("pose.mapId: 문자열 또는 null이어야 합니다")
        pose = {
            "x": float(pose["x"]),
            "y": float(pose["y"]),
            "yaw": float(pose["yaw"]),
            "mapId": pose["mapId"],
        }

    return track_ids, confidence, pose


def parse_encounter_message(message: str | dict[str, Any]) -> EncounterContext:
    """공통 Encounter 본문 또는 31-5 Envelope 문자열을 검증한다."""

    if isinstance(message, str):
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as error:
            raise EncounterContractError("JSON 해석 실패") from error
    elif isinstance(message, dict):
        payload = message
    else:
        raise EncounterContractError("메시지는 JSON 문자열 또는 객체여야 합니다")

    if not isinstance(payload, dict):
        raise EncounterContractError("최상위 JSON은 객체여야 합니다")
    data = payload.get("data") if "data" in payload else payload
    if not isinstance(data, dict):
        raise EncounterContractError("data는 Encounter 객체여야 합니다")

    missing = REQUIRED_FIELDS - set(data)
    if missing:
        raise EncounterContractError(
            f"필수 필드 누락: {', '.join(sorted(missing))}"
        )
    unexpected = set(data) - ALLOWED_FIELDS
    if unexpected:
        raise EncounterContractError(
            f"허용되지 않은 필드: {', '.join(sorted(unexpected))}"
        )

    encounter_id = _require_uuid(data["encounterId"], "encounterId")
    try:
        phase = EncounterPhase(data["phase"])
    except (TypeError, ValueError) as error:
        raise EncounterContractError("phase: 허용되지 않은 값입니다") from error

    detected_at = data["detectedAt"]
    if not isinstance(detected_at, str) or not UTC_PATTERN.fullmatch(detected_at):
        raise EncounterContractError("detectedAt: UTC 시각 형식이 아닙니다")

    person_count = data["personCount"]
    if (
        isinstance(person_count, bool)
        or not isinstance(person_count, int)
        or person_count < 0
    ):
        raise EncounterContractError("personCount: 0 이상의 정수여야 합니다")

    mission_id = _require_uuid(
        data.get("missionId"),
        "missionId",
        nullable=True,
    )
    track_ids, confidence, pose = _optional_fields(data)

    return EncounterContext(
        encounter_id=encounter_id,
        phase=phase,
        detected_at=detected_at,
        person_count=person_count,
        mission_id=mission_id,
        track_ids=track_ids,
        confidence=confidence,
        pose=pose,
    )


class EncounterSessionState(str, Enum):
    """Encounter 하나에 대한 음성 세션 조정 상태."""

    IDLE = "IDLE"
    WAITING_APPROACH = "WAITING_APPROACH"
    INTERACTION_ACTIVE = "INTERACTION_ACTIVE"
    # 유실을 봤지만 아직 포기하지 않은 상태 (S15P11A301-332).
    #
    # mission_manager 가 사후 3초 창 안의 재감지를 INTERACTING 으로 되돌리는데,
    # 이 상태가 없던 동안 voice 는 첫 LOST 를 종결로 처리했다. 실측에서 유실
    # 구간이 **0.179초**였고, 그 뒤 답까지 받아놓고(`[STT] 나 보여?`) 버렸다.
    # 8.4 FPS 에서는 프레임 두어 장이면 0.2초다.
    #
    # 창 길이를 여기서 세지 않는다. mission_manager 가 창의 주인이고, 그 창이
    # 닫히면 상태가 POST_RECORDING 을 벗어난다 — 그것을 신호로 쓴다. 값을
    # 복사하면 두 노드가 다시 어긋날 수 있고, 어긋남이 이 결함의 원인이었다.
    LOST_GRACE = "LOST_GRACE"
    WAITING_ENDED = "WAITING_ENDED"
    COMPLETED = "COMPLETED"
    LOST = "LOST"
    ABORTED_MANUAL = "ABORTED_MANUAL"
    ABORTED_SAFETY = "ABORTED_SAFETY"


@dataclass(frozen=True)
class EncounterSessionOutcome:
    """Encounter 사건 처리 뒤 외부 어댑터가 수행할 작업."""

    state: EncounterSessionState
    accepted: bool
    start_conversation: bool = False
    abort_conversation: bool = False
    handoff_report: bool = False
    notify_dialogue_completed: bool = False
    report_context: dict[str, Any] | None = None
    detail: str = ""


@dataclass(frozen=True)
class EncounterSessionExecution:
    """상태 결과를 외부 대화·보고·Mission Manager 경계에 전달한 결과."""

    conversation_started: bool = False
    conversation_aborted: bool = False
    report_handed_off: bool = False
    dialogue_completion_notified: bool = False
    errors: tuple[str, ...] = ()


def execute_encounter_outcome(
    outcome: EncounterSessionOutcome,
    *,
    start_conversation: Callable[[], Any],
    abort_conversation: Callable[[], Any],
    handoff_report: Callable[[dict[str, Any]], Any],
    notify_dialogue_completed: Callable[[dict[str, Any]], Any],
) -> EncounterSessionExecution:
    """상태 결과가 요청한 외부 작업만 호출하고 어댑터 예외를 격리한다.

    실제 ROS 2 구독 콜백에서는 긴 대화를 직접 실행하지 않고 작업 큐에 넘겨야 한다.
    이 함수의 콜백은 그 작업 큐 또는 테스트 대역이 된다.
    """

    started = False
    aborted = False
    handed_off = False
    notified = False
    errors: list[str] = []

    def invoke(name: str, callback: Callable[..., Any], *args: Any) -> bool:
        try:
            callback(*args)
            return True
        except Exception as error:
            errors.append(f"{name}:{type(error).__name__}")
            return False

    if outcome.start_conversation:
        started = invoke("start_conversation", start_conversation)
    if outcome.abort_conversation:
        aborted = invoke("abort_conversation", abort_conversation)
    if outcome.handoff_report:
        handed_off = invoke(
            "handoff_report",
            handoff_report,
            dict(outcome.report_context or {}),
        )
    if outcome.notify_dialogue_completed:
        notified = invoke(
            "notify_dialogue_completed",
            notify_dialogue_completed,
            dict(outcome.report_context or {}),
        )

    return EncounterSessionExecution(
        conversation_started=started,
        conversation_aborted=aborted,
        report_handed_off=handed_off,
        dialogue_completion_notified=notified,
        errors=tuple(errors),
    )


class EncounterSessionCoordinator:
    """APPROACHED 이후 encounter당 음성 세션을 한 번만 시작한다."""

    def __init__(self) -> None:
        self.state = EncounterSessionState.IDLE
        self.context: EncounterContext | None = None
        self.completed_encounters: set[str] = set()

    def handle(self, context: EncounterContext) -> EncounterSessionOutcome:
        """Mission Manager가 발행한 Encounter 사건을 처리한다."""

        if context.encounter_id in self.completed_encounters:
            return self._outcome(False, detail="이미 종료한 encounter의 중복 사건")

        if self.context is None:
            if context.phase is not EncounterPhase.CONFIRMED:
                return self._outcome(False, detail="CONFIRMED보다 먼저 온 사건")
            self.context = context
            self.state = EncounterSessionState.WAITING_APPROACH
            return self._outcome(True, detail="encounter 등록, 안전 위치 정지 대기")

        if context.encounter_id != self.context.encounter_id:
            return self._outcome(False, detail="다른 encounter가 진행 중")

        if context.phase is EncounterPhase.CONFIRMED:
            return self._outcome(False, detail="중복 CONFIRMED")

        if context.phase is EncounterPhase.APPROACHED:
            if self.state is EncounterSessionState.LOST_GRACE:
                # 유예 중 APPROACHED 가 다시 오면 재감지와 같게 다룬다.
                # mission_manager 가 REDETECTED 대신 이것을 낼 경로가 있다.
                self.state = EncounterSessionState.INTERACTION_ACTIVE
                return self._outcome(True, detail="유예 중 재접근 확인, 대화를 잇는다")
            if self.state is not EncounterSessionState.WAITING_APPROACH:
                return self._outcome(False, detail="현재 상태에서 APPROACHED 무시")
            # 이후 사건에 personCount=0이 올 수 있으므로 최초 탐지 문맥을 보존한다.
            self.state = EncounterSessionState.INTERACTION_ACTIVE
            return self._outcome(
                True,
                start_conversation=True,
                detail="안전 위치 정지 확인, 음성 세션 시작",
            )

        if context.phase is EncounterPhase.LOST:
            # 대화 중 유실이면 **포기하지 않고 유예에 들어간다**(S15P11A301-332).
            # 창의 주인은 mission_manager 다 — 창이 닫히면 상태가 POST_RECORDING
            # 을 벗어나고, 그때 `lost_grace_closed()` 가 종결한다.
            if self.state is EncounterSessionState.INTERACTION_ACTIVE:
                self.state = EncounterSessionState.LOST_GRACE
                return self._outcome(
                    True,
                    detail="사람 유실, 재감지 유예 시작(대화는 유지)",
                )
            # 대화 중이 아니었으면 종전대로 즉시 종결한다. 유예해서 얻을 것이
            # 없고, 접근 대기 중 유실은 다시 CONFIRMED 부터 시작하는 편이 맞다.
            self.state = EncounterSessionState.LOST
            self._finish_current()
            return self._outcome(
                True,
                detail="사람 유실, 자동 재개 없이 안전 종료",
            )

        if context.phase is EncounterPhase.ENDED:
            if self.state is not EncounterSessionState.WAITING_ENDED:
                return self._outcome(False, detail="대화 완료 통지 전 ENDED 무시")
            self.state = EncounterSessionState.COMPLETED
            self._finish_current()
            return self._outcome(True, detail="Mission Manager ENDED 확인")

        # REDETECTED
        if self.state is EncounterSessionState.LOST_GRACE:
            self.state = EncounterSessionState.INTERACTION_ACTIVE
            return self._outcome(True, detail="재감지 확인, 대화를 잇는다")

        return self._outcome(False, detail="REDETECTED는 새 음성 세션을 만들지 않음")

    def startable(self, encounter_id: str) -> bool:
        """그 encounter 로 지금 세션을 시작해도 되는지 (S15P11A301-334).

        중단 배수가 끝난 뒤 큐에 담아 둔 문맥을 꺼낼 때 쓴다. 배수는 진행 중인
        녹음·STT 주기를 마쳐야 끝나므로 최대 10초가 걸리고, 그 사이에 그
        encounter 가 다시 유실되거나 종료됐을 수 있다. 확인 없이 시작하면 이미
        떠난 사람에게 말을 건다.
        """
        return (
            self.state is EncounterSessionState.INTERACTION_ACTIVE
            and self.context is not None
            and self.context.encounter_id == encounter_id
        )

    def lost_grace_closed(self) -> EncounterSessionOutcome:
        """유예 창이 닫혔다 — mission 이 사후 녹화 상태를 벗어났다.

        창의 길이를 이 클래스가 세지 않는 이유는 `LOST_GRACE` 주석에 있다.
        호출부는 mission status 를 보고 판단한다.
        """
        if self.state is not EncounterSessionState.LOST_GRACE:
            return self._outcome(False, detail="유예 중이 아니다")
        self.state = EncounterSessionState.LOST
        self._finish_current()
        return self._outcome(
            True,
            abort_conversation=True,
            detail="재감지 유예 만료, 대화를 종결한다",
        )

    def conversation_finished(
        self,
        *,
        termination_reason: str = "NORMAL",
    ) -> EncounterSessionOutcome:
        """대화 결과를 보고 경계와 Mission Manager 통지 경계에 인계한다."""

        if (
            self.context is None
            or self.state is not EncounterSessionState.INTERACTION_ACTIVE
        ):
            return self._outcome(False, detail="진행 중인 음성 세션이 없음")
        self.state = EncounterSessionState.WAITING_ENDED
        return self._outcome(
            True,
            handoff_report=True,
            notify_dialogue_completed=True,
            report_context=self.context.report_context(),
            detail=f"대화 종료({termination_reason}), 보고 인계 및 ENDED 결정 대기",
        )

    def abort_manual(self) -> EncounterSessionOutcome:
        return self._abort(EncounterSessionState.ABORTED_MANUAL, "수동 중단")

    def abort_safety(self) -> EncounterSessionOutcome:
        return self._abort(EncounterSessionState.ABORTED_SAFETY, "안전 중단")

    def _abort(
        self,
        state: EncounterSessionState,
        detail: str,
    ) -> EncounterSessionOutcome:
        if self.context is None:
            return self._outcome(False, detail="중단할 encounter가 없음")
        was_active = self.state is EncounterSessionState.INTERACTION_ACTIVE
        self.state = state
        self._finish_current()
        return self._outcome(
            True,
            abort_conversation=was_active,
            detail=f"{detail}, 자동 재개 금지",
        )

    def _finish_current(self) -> None:
        if self.context is not None:
            self.completed_encounters.add(self.context.encounter_id)
            self.context = None

    def _outcome(
        self,
        accepted: bool,
        *,
        start_conversation: bool = False,
        abort_conversation: bool = False,
        handoff_report: bool = False,
        notify_dialogue_completed: bool = False,
        report_context: dict[str, Any] | None = None,
        detail: str = "",
    ) -> EncounterSessionOutcome:
        return EncounterSessionOutcome(
            state=self.state,
            accepted=accepted,
            start_conversation=start_conversation,
            abort_conversation=abort_conversation,
            handoff_report=handoff_report,
            notify_dialogue_completed=notify_dialogue_completed,
            report_context=report_context,
            detail=detail,
        )

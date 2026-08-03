"""관제 보고 ACK와 Mission Manager 탐사 재개 승인을 결합하는 상태머신.

⚠️ 미배선 (2026-08-01) — 로봇 다수 투입으로 관제가 ACK를 내리지 않는 것이
확정되어, ACK를 기다리는 이 모듈의 전제가 현재 아키텍처에서 성립하지 않는다.
어느 실행 경로에도 연결되어 있지 않으며(S15P11A301-182 재평가 대상),
guide_code 매핑도 146 v2에서 삭제된 문구를 참조한다. 배선 전 갱신이 필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .guide_audio import GuideCode


class LifecycleState(str, Enum):
    """음성 보고 종료 절차 상태."""

    WAITING_REPORT_ACK = "WAITING_REPORT_ACK"
    WAITING_RESUME_DECISION = "WAITING_RESUME_DECISION"
    READY_TO_RESUME = "READY_TO_RESUME"
    REPORT_CONFIRMED_STAY = "REPORT_CONFIRMED_STAY"
    REPORT_PENDING = "REPORT_PENDING"
    DELIVERY_FAILED = "DELIVERY_FAILED"


class LifecycleEventType(str, Enum):
    """외부 어댑터가 상태머신에 전달하는 사건."""

    REPORT_ACK_SUCCEEDED = "REPORT_ACK_SUCCEEDED"
    REPORT_ACK_FAILED = "REPORT_ACK_FAILED"
    RESUME_APPROVED = "RESUME_APPROVED"
    RESUME_REJECTED = "RESUME_REJECTED"
    CLOSING_TIMEOUT = "CLOSING_TIMEOUT"


@dataclass(frozen=True)
class LifecycleEvent:
    """보고 단위를 구분하기 위해 모든 사건에 동일한 report_id를 사용한다."""

    event_type: LifecycleEventType
    report_id: str


@dataclass(frozen=True)
class LifecycleOutcome:
    """한 사건을 처리한 뒤 외부 계층이 수행할 동작."""

    state: LifecycleState
    accepted: bool
    guide_code: GuideCode | None = None
    request_exploration_resume: bool = False
    detail: str = ""


@dataclass(frozen=True)
class LifecycleExecution:
    """상태 결과를 안내 음성과 Mission Manager 요청으로 전달한 결과."""

    playback_result: Any | None
    resume_request_sent: bool
    resume_request_accepted: bool | None
    resume_request_error: str | None = None


def execute_outcome(
    outcome: LifecycleOutcome,
    *,
    guide_player: Any,
    request_mission_resume: Callable[[], bool],
) -> LifecycleExecution:
    """상태 결과를 실행 경계에 전달한다.

    이 함수는 모터 명령을 만들지 않는다. 두 승인이 모두 확인된 결과에서만
    Mission Manager의 재개 요청 콜백을 호출한다.
    """

    playback_result = None
    if outcome.guide_code is not None:
        report_succeeded = outcome.state in {
            LifecycleState.READY_TO_RESUME,
            LifecycleState.REPORT_CONFIRMED_STAY,
        }
        playback_result = guide_player.play(
            outcome.guide_code,
            report_succeeded=report_succeeded,
            exploration_resume_approved=(
                outcome.state == LifecycleState.READY_TO_RESUME
            ),
        )

    if not outcome.request_exploration_resume:
        return LifecycleExecution(playback_result, False, None)

    try:
        accepted = bool(request_mission_resume())
        return LifecycleExecution(playback_result, True, accepted)
    except Exception as error:
        return LifecycleExecution(
            playback_result,
            True,
            False,
            f"{type(error).__name__}",
        )


class ReportLifecycle:
    """ACK와 재개 승인을 순서와 중복에 무관하게 한 번만 처리한다.

    ACK 직후 바로 성공 음성을 재생하지 않고 짧은 Closing 대기 구간 동안 로컬
    Mission Manager의 재개 결정을 기다린다. 둘 다 확인되면 결합형 안내를 한 번만
    재생한다. 재개가 거부되거나 Closing timeout이 되면 성공 안내만 재생하고 정지한다.
    """

    def __init__(self, report_id: str) -> None:
        if not report_id:
            raise ValueError("report_id는 비어 있을 수 없습니다")
        self.report_id = report_id
        self.state = LifecycleState.WAITING_REPORT_ACK
        self.report_acknowledged = False
        self.resume_approved: bool | None = None
        self._terminal = False

    def handle(self, event: LifecycleEvent) -> LifecycleOutcome:
        if event.report_id != self.report_id:
            return self._outcome(False, detail="다른 보고서의 이벤트")
        if self._terminal:
            return self._outcome(False, detail="이미 종료된 보고 절차의 중복 이벤트")

        handlers = {
            LifecycleEventType.REPORT_ACK_SUCCEEDED: self._ack_succeeded,
            LifecycleEventType.REPORT_ACK_FAILED: self._ack_failed,
            LifecycleEventType.RESUME_APPROVED: self._resume_approved,
            LifecycleEventType.RESUME_REJECTED: self._resume_rejected,
            LifecycleEventType.CLOSING_TIMEOUT: self._closing_timeout,
        }
        return handlers[event.event_type]()

    def _ack_succeeded(self) -> LifecycleOutcome:
        if self.report_acknowledged:
            return self._outcome(False, detail="중복 관제 ACK")
        self.report_acknowledged = True
        if self.resume_approved is True:
            return self._complete_departure()
        if self.resume_approved is False:
            return self._complete_stay("탐사 재개 거부 후 관제 ACK 확인")
        self.state = LifecycleState.WAITING_RESUME_DECISION
        return self._outcome(True, detail="관제 ACK 확인, 탐사 재개 결정 대기")

    def _ack_failed(self) -> LifecycleOutcome:
        if self.report_acknowledged:
            return self._outcome(False, detail="성공 ACK 뒤 도착한 충돌 이벤트")
        self.state = LifecycleState.DELIVERY_FAILED
        self._terminal = True
        return self._outcome(
            True,
            guide_code=GuideCode.NETWORK_WAIT,
            detail="관제 보고 실패 확인",
        )

    def _resume_approved(self) -> LifecycleOutcome:
        if self.resume_approved is True:
            return self._outcome(False, detail="중복 탐사 재개 승인")
        if self.resume_approved is False:
            return self._outcome(False, detail="재개 거부 뒤 도착한 충돌 이벤트")
        self.resume_approved = True
        if self.report_acknowledged:
            return self._complete_departure()
        return self._outcome(True, detail="탐사 재개 승인, 관제 ACK 대기")

    def _resume_rejected(self) -> LifecycleOutcome:
        if self.resume_approved is False:
            return self._outcome(False, detail="중복 탐사 재개 거부")
        if self.resume_approved is True:
            return self._outcome(False, detail="재개 승인 뒤 도착한 충돌 이벤트")
        self.resume_approved = False
        if self.report_acknowledged:
            return self._complete_stay("관제 ACK 확인, 탐사 재개 거부")
        return self._outcome(True, detail="탐사 재개 거부, 관제 ACK 대기")

    def _closing_timeout(self) -> LifecycleOutcome:
        if self.report_acknowledged:
            return self._complete_stay("재개 결정 제한시간 초과")
        self.state = LifecycleState.REPORT_PENDING
        self._terminal = True
        return self._outcome(
            True,
            guide_code=GuideCode.REPORT_PENDING,
            detail="관제 ACK 제한시간 초과, 보고는 대기열에 유지",
        )

    def _complete_departure(self) -> LifecycleOutcome:
        self.state = LifecycleState.READY_TO_RESUME
        self._terminal = True
        return self._outcome(
            True,
            guide_code=GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            request_exploration_resume=True,
            detail="관제 ACK와 탐사 재개 승인 모두 확인",
        )

    def _complete_stay(self, detail: str) -> LifecycleOutcome:
        self.state = LifecycleState.REPORT_CONFIRMED_STAY
        self._terminal = True
        return self._outcome(
            True,
            guide_code=GuideCode.REPORT_SUCCEEDED,
            detail=detail,
        )

    def _outcome(
        self,
        accepted: bool,
        *,
        guide_code: GuideCode | None = None,
        request_exploration_resume: bool = False,
        detail: str = "",
    ) -> LifecycleOutcome:
        return LifecycleOutcome(
            self.state,
            accepted,
            guide_code,
            request_exploration_resume,
            detail,
        )

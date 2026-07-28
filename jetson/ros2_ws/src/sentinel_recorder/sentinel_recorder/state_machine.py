"""녹화 전용 상태 머신 (S15P11A301-123, 명세 32-5 「녹화 상태」).

    [*] --> BUFFERING
    BUFFERING --> RECORDING: 사람 확정
    RECORDING --> INTERACTION: 안전 위치 정지
    INTERACTION --> POST_RECORDING: 대화 종료
    POST_RECORDING --> INTERACTION: 사람·음성 재감지
    POST_RECORDING --> FINALIZING: 3초 경과
    FINALIZING --> UPLOAD_PENDING: 로컬 저장 성공
    FINALIZING --> RECORDING_FAILED: 저장 실패
    UPLOAD_PENDING --> AVAILABLE: 업로드 성공

**이것은 임무 상태 머신과 다른 상태 공간이다.** `INTERACTION`은 녹화 구간이고
임무 상태 머신(14.1, 26.2)의 `INTERACTING`과 이름만 비슷하다. 명세가 "서로
치환해 쓰지 않는다"고 못박았다.

ROS도 파일 시스템도 모른다. 시간은 호출자가 주입한다. 그래서 5분 타임아웃을
5분 기다리지 않고 시험할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


class RecordingState(str, Enum):
    BUFFERING = 'BUFFERING'
    RECORDING = 'RECORDING'
    INTERACTION = 'INTERACTION'
    POST_RECORDING = 'POST_RECORDING'
    FINALIZING = 'FINALIZING'
    UPLOAD_PENDING = 'UPLOAD_PENDING'
    RECORDING_FAILED = 'RECORDING_FAILED'


class EndReason(str, Enum):
    """이벤트가 끝난 사유. 보고서와 관제 표시에 쓴다."""

    NORMAL = 'NORMAL'
    NO_RESPONSE_TIMEOUT = 'NO_RESPONSE_TIMEOUT'
    MAX_DURATION = 'MAX_DURATION'
    PERSON_LOST = 'PERSON_LOST'


class Phase(str, Enum):
    """`common/schemas/encounter.schema.json`의 phase."""

    CONFIRMED = 'CONFIRMED'
    APPROACHED = 'APPROACHED'
    ENDED = 'ENDED'
    REDETECTED = 'REDETECTED'
    LOST = 'LOST'


# 32-5 「종료 예외」
POST_RECORDING_SECONDS = 3
NO_RESPONSE_TIMEOUT_SECONDS = 30
MAX_EVENT_SECONDS = 300


@dataclass
class Event:
    """진행 중인 이벤트."""

    encounter_id: str
    detected_at: datetime
    started_at: datetime
    state: RecordingState = RecordingState.RECORDING
    end_reason: EndReason | None = None
    person_count: int = 0
    mission_id: str | None = None
    # 마지막으로 상호작용 상태가 바뀐 시각. NO_RESPONSE_TIMEOUT 판정 기준이다.
    last_activity_at: datetime | None = None
    # POST_RECORDING에 들어간 시각. 3초 경과 판정 기준이다.
    post_started_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RecordingStateMachine:
    """이벤트 하나를 추적한다.

    동시에 여러 이벤트를 다루지 않는다. 32-6이 "동시에 발견된 사람들은 하나의
    encounter를 사용한다"고 정했으므로 진행 중 이벤트는 항상 한 개다. 다른
    `encounterId`가 오면 새 이벤트가 아니라 무시한다. 두 이벤트가 같은 조각을
    나눠 가지면 어느 쪽 MP4에 넣을지 정할 수 없다.
    """

    def __init__(
        self,
        post_recording_seconds: int = POST_RECORDING_SECONDS,
        no_response_timeout_seconds: int = NO_RESPONSE_TIMEOUT_SECONDS,
        max_event_seconds: int = MAX_EVENT_SECONDS,
    ) -> None:
        self.post_recording_seconds = post_recording_seconds
        self.no_response_timeout_seconds = no_response_timeout_seconds
        self.max_event_seconds = max_event_seconds
        self.state = RecordingState.BUFFERING
        self.event: Event | None = None

    # ------------------------------------------------------------------
    # 외부 신호
    # ------------------------------------------------------------------

    def on_encounter(
        self,
        encounter_id: str,
        phase: Phase,
        detected_at: datetime,
        now: datetime,
        person_count: int = 0,
        mission_id: str | None = None,
    ) -> str | None:
        """encounter 신호를 처리한다. 상태가 바뀌면 전이 이름을 돌려준다."""
        if phase is Phase.CONFIRMED:
            return self._on_confirmed(
                encounter_id, detected_at, now, person_count, mission_id
            )

        # 진행 중 이벤트가 없거나 다른 이벤트의 신호는 무시한다.
        if self.event is None or self.event.encounter_id != encounter_id:
            return None

        if phase is Phase.APPROACHED:
            return self._to_interaction(now, 'APPROACHED')
        if phase is Phase.ENDED:
            return self._to_post_recording(now)
        if phase is Phase.REDETECTED:
            return self._on_redetected(now)
        if phase is Phase.LOST:
            return self._on_lost(now)
        return None

    def _on_confirmed(
        self,
        encounter_id: str,
        detected_at: datetime,
        now: datetime,
        person_count: int,
        mission_id: str | None,
    ) -> str | None:
        if self.event is not None:
            # 같은 이벤트의 재확정이면 사람 수만 갱신한다. 32-6에 따라 동시에
            # 발견된 사람들은 encounter 하나를 공유하므로 CONFIRMED가 여러 번
            # 올 수 있다. 그때 새 녹화를 시작하면 이벤트가 쪼개진다.
            if self.event.encounter_id == encounter_id:
                self.event.person_count = max(self.event.person_count, person_count)
                self.event.last_activity_at = now
                return None
            # 다른 이벤트가 진행 중이다. 새로 시작하지 않는다.
            return None

        self.event = Event(
            encounter_id=encounter_id,
            detected_at=detected_at,
            started_at=now,
            state=RecordingState.RECORDING,
            person_count=person_count,
            mission_id=mission_id,
            last_activity_at=now,
        )
        self.state = RecordingState.RECORDING
        return 'BUFFERING->RECORDING'

    def _to_interaction(self, now: datetime, _cause: str) -> str | None:
        if self.state not in (
            RecordingState.RECORDING,
            RecordingState.POST_RECORDING,
        ):
            return None
        previous = self.state
        self.state = RecordingState.INTERACTION
        assert self.event is not None
        self.event.state = self.state
        self.event.last_activity_at = now
        self.event.post_started_at = None
        return f'{previous.value}->INTERACTION'

    def _to_post_recording(self, now: datetime) -> str | None:
        if self.state not in (
            RecordingState.RECORDING,
            RecordingState.INTERACTION,
        ):
            return None
        previous = self.state
        self.state = RecordingState.POST_RECORDING
        assert self.event is not None
        self.event.state = self.state
        self.event.post_started_at = now
        self.event.last_activity_at = now
        return f'{previous.value}->POST_RECORDING'

    def _on_redetected(self, now: datetime) -> str | None:
        """사후 3초 안의 재감지만 INTERACTION으로 되돌린다(32-5 종료 예외).

        3초가 지난 뒤의 재감지는 새 이벤트로 봐야 한다. 되돌리면 이벤트가
        무한히 늘어나 MAX_DURATION까지 갈 수 있다.
        """
        if self.state is not RecordingState.POST_RECORDING:
            return None
        assert self.event is not None
        if self.event.post_started_at is None:
            return None
        elapsed = (now - self.event.post_started_at).total_seconds()
        if elapsed > self.post_recording_seconds:
            return None
        return self._to_interaction(now, 'REDETECTED')

    def _on_lost(self, now: datetime) -> str | None:
        if self.event is None:
            return None
        if self.state in (
            RecordingState.RECORDING,
            RecordingState.INTERACTION,
        ):
            self.event.end_reason = EndReason.PERSON_LOST
            return self._to_post_recording(now)
        return None

    # ------------------------------------------------------------------
    # 시간 기반 전이
    # ------------------------------------------------------------------

    def tick(self, now: datetime) -> str | None:
        """주기적으로 호출한다. 시간으로 결정되는 전이를 처리한다.

        순서가 중요하다. MAX_DURATION을 먼저 본다. 5분을 넘긴 이벤트는 어떤
        상태에 있든 닫아야 하고, POST_RECORDING 3초 판정보다 우선한다.
        """
        if self.event is None:
            return None

        elapsed = (now - self.event.started_at).total_seconds()
        if elapsed >= self.max_event_seconds:
            self.event.end_reason = EndReason.MAX_DURATION
            return self._to_finalizing()

        if self.state is RecordingState.POST_RECORDING:
            assert self.event.post_started_at is not None
            post_elapsed = (now - self.event.post_started_at).total_seconds()
            if post_elapsed >= self.post_recording_seconds:
                if self.event.end_reason is None:
                    self.event.end_reason = EndReason.NORMAL
                return self._to_finalizing()
            return None

        if self.state in (RecordingState.RECORDING, RecordingState.INTERACTION):
            reference = self.event.last_activity_at or self.event.started_at
            idle = (now - reference).total_seconds()
            if idle >= self.no_response_timeout_seconds:
                self.event.end_reason = EndReason.NO_RESPONSE_TIMEOUT
                return self._to_post_recording(now)
        return None

    def _to_finalizing(self) -> str:
        previous = self.state
        self.state = RecordingState.FINALIZING
        assert self.event is not None
        self.event.state = self.state
        return f'{previous.value}->FINALIZING'

    # ------------------------------------------------------------------
    # 마무리 결과
    # ------------------------------------------------------------------

    def finish(self, succeeded: bool) -> str:
        previous = self.state
        self.state = (
            RecordingState.UPLOAD_PENDING if succeeded else RecordingState.RECORDING_FAILED
        )
        if self.event is not None:
            self.event.state = self.state
        transition = f'{previous.value}->{self.state.value}'
        # 이벤트를 놓고 BUFFERING으로 돌아간다. 다음 사람 확정을 받을 준비다.
        self.event = None
        self.state = RecordingState.BUFFERING
        return transition

    @property
    def recording(self) -> bool:
        """조각을 계속 모아야 하는 상태인가."""
        return self.state in (
            RecordingState.RECORDING,
            RecordingState.INTERACTION,
            RecordingState.POST_RECORDING,
        )

    def deadline_hint(self, now: datetime) -> datetime | None:
        """다음 시간 기반 전이가 일어날 시각. 로그와 시험에 쓴다."""
        if self.event is None:
            return None
        candidates = [
            self.event.started_at + timedelta(seconds=self.max_event_seconds)
        ]
        if self.state is RecordingState.POST_RECORDING and self.event.post_started_at:
            candidates.append(
                self.event.post_started_at
                + timedelta(seconds=self.post_recording_seconds)
            )
        elif self.event.last_activity_at:
            candidates.append(
                self.event.last_activity_at
                + timedelta(seconds=self.no_response_timeout_seconds)
            )
        future = [item for item in candidates if item > now]
        return min(future) if future else None

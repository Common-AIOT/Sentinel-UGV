"""임무 상태 머신 (S15P11A301-133, 명세 26장).

26.1이 정한 단일 권한 원칙의 핵심이다. Nav2·AI·음성·관제가 상태를 직접 바꾸지
않고 신호를 보내며, 그 신호를 상태 전이로 바꾸는 것은 이 클래스뿐이다.

## encounter phase는 별 상태 머신이 아니다

32-5의 녹화 phase와 26.3의 임무 상태 전이는 같은 사건의 두 표현이다.

    26.3 전이                                    발행할 phase
    EXPLORING → PERSON_APPROACHING               CONFIRMED
    PERSON_APPROACHING → INTERACTING             APPROACHED
    INTERACTING → POST_RECORDING                 ENDED
    POST_RECORDING → INTERACTING (재감지)         REDETECTED
    진행 중 사람 상실                              LOST

그래서 phase를 따로 계산하지 않고 전이의 부산물로 낸다. 두 상태 머신을 두면 서로
어긋날 때 어느 쪽이 맞는지 알 수 없다.

`sentinel_recorder`의 `RecordingStateMachine`과도 역할이 다르다. 그쪽은 녹화 자원
(조각 수집, MP4 생성, 디스크 상한)을 관리하고 이쪽은 임무 상태만 판단한다. 녹화
쪽 상태를 이쪽이 알 필요가 없고, 알면 녹화 실패가 주행을 멈추게 된다.

## 시간을 주입한다

`POST_RECORDING` 3초와 무응답 타임아웃을 실제로 기다리지 않고 시험하기 위해서다.
`sentinel_recorder`의 상태 머신과 같은 방식이다.

## MVP 범위

26.2의 12개 상태 중 encounter 경로와 안전 전이를 구현한다.

    구현      SAFE_IDLE, EXPLORING, PERSON_APPROACHING, INTERACTING,
              POST_RECORDING, REPORTING, PAUSED, ESTOP, ERROR
    자리만    MANUAL, RETURNING, COMPLETED

`MANUAL`은 control session과 gamepad deadman이 필요하고(36장), `RETURNING`은
home pose와 Nav2 목표 전송이 필요하다(23.5). 둘 다 이 티켓 범위 밖이므로 상태
값만 두고 전이 트리거를 받지 않는다. 받지 않는 것을 조용히 무시하지 않고
`ignored_reason`으로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class MissionState(str, Enum):
    """26.2 최종 상태."""

    SAFE_IDLE = 'SAFE_IDLE'
    EXPLORING = 'EXPLORING'
    PERSON_APPROACHING = 'PERSON_APPROACHING'
    INTERACTING = 'INTERACTING'
    POST_RECORDING = 'POST_RECORDING'
    REPORTING = 'REPORTING'
    PAUSED = 'PAUSED'
    MANUAL = 'MANUAL'
    RETURNING = 'RETURNING'
    COMPLETED = 'COMPLETED'
    ESTOP = 'ESTOP'
    ERROR = 'ERROR'


class Signal(str, Enum):
    """`/mission/signal`로 오는 신호. mission-signal.schema.json과 같아야 한다."""

    MISSION_START = 'MISSION_START'
    SAFE_POSE_REACHED = 'SAFE_POSE_REACHED'
    APPROACH_FAILED = 'APPROACH_FAILED'
    DIALOGUE_ENDED = 'DIALOGUE_ENDED'
    REPORT_COMMITTED = 'REPORT_COMMITTED'
    RESUME_REQUESTED = 'RESUME_REQUESTED'
    PAUSE_REQUESTED = 'PAUSE_REQUESTED'
    RESUME_APPROVED = 'RESUME_APPROVED'
    ESTOP = 'ESTOP'
    SENSOR_FAULT = 'SENSOR_FAULT'
    # 관제의 STOP 명령(27.4). 26.3의 COMPLETED로 보낸다.
    #
    # cmd/mission 의 STOP 에 대응하는 신호가 없어서 추가했다(S15P11A301-143).
    # RETURN 은 추가하지 않았다. RETURNING 은 home pose 복귀 주행이 필요해
    # UNIMPLEMENTED 로 남아 있고, 신호만 만들면 갈 수 없는 상태로 보내게 된다.
    MISSION_COMPLETED = 'MISSION_COMPLETED'


class Phase(str, Enum):
    """`/perception/encounter`로 나갈 phase. encounter.schema.json과 같아야 한다."""

    CONFIRMED = 'CONFIRMED'
    APPROACHED = 'APPROACHED'
    ENDED = 'ENDED'
    REDETECTED = 'REDETECTED'
    LOST = 'LOST'


# 26.2 표의 "이동 허용" 열. 주행 노드가 state를 각자 해석하지 않도록 여기서 정한다.
# 상태를 추가할 때 이 표를 빠뜨리면 KeyError가 나므로 조용히 잘못 동작하지 않는다.
MOVEMENT: dict[MissionState, tuple[bool, float | None]] = {
    MissionState.SAFE_IDLE: (False, None),
    MissionState.EXPLORING: (True, None),
    # 30.3이 접근 속도를 0.10m/s 이하로 제한한다.
    MissionState.PERSON_APPROACHING: (True, 0.10),
    MissionState.INTERACTING: (False, None),
    MissionState.POST_RECORDING: (False, None),
    MissionState.REPORTING: (False, None),
    MissionState.PAUSED: (False, None),
    # deadman이 눌린 동안만 허용한다(26.2). 그 판단은 조종 노드가 하며 여기서는
    # 상태만으로 허용하지 않는다.
    MissionState.MANUAL: (False, None),
    MissionState.RETURNING: (True, None),
    MissionState.COMPLETED: (False, None),
    MissionState.ESTOP: (False, None),
    MissionState.ERROR: (False, None),
}

# 이 티켓에서 전이 트리거를 받지 않는 상태. 36장·23.5가 필요하다.
UNIMPLEMENTED = frozenset({MissionState.MANUAL, MissionState.RETURNING})

# 종료 절차에 들어간 상태. 사람이 안 보이는 것이 정상이므로 상실로 다시 끝내지
# 않는다.
#
# `REPORTING`을 빠뜨렸다가 무한 루프를 만들었다(S15P11A301-139). 보고 단계는 사람이
# 이미 떠난 뒤인데 상실을 다시 처리해 `POST_RECORDING`으로 되돌아갔다.
#
# 목록으로 두는 이유는 상태가 늘 때 함께 검토하게 하기 위해서다. 조건문에 상태
# 이름을 직접 쓰면 새 종료 상태가 추가될 때 이 검사에서 빠진다.
# COMMAND_ACK 의 reasonCode (31-6). 관제가 이 값으로 화면을 분기한다.
#
# 문자열을 여기 모아 두는 이유는 백엔드·관제와 공유하는 값이기 때문이다. 코드
# 안에 흩어 두면 오타를 잡을 수 없고, 관제가 어떤 값이 올 수 있는지 알 수 없다.
REASON_ESTOP_ACTIVE = 'ESTOP_ACTIVE'
REASON_ERROR_LATCHED = 'ERROR_LATCHED'
REASON_INVALID_STATE = 'INVALID_STATE'
REASON_DUPLICATE_COMMAND = 'DUPLICATE_COMMAND'
REASON_NOT_IMPLEMENTED = 'NOT_IMPLEMENTED'

# COMMAND_ACK 의 status (31-6). command-ack.schema.json 의 enum 과 같아야 한다.
#
# ACCEPTED 는 여기서 쓰지 않는다. 접수만 하고 나중에 끝나는 명령이 RETURN(복귀
# 주행)뿐이고 그것은 미구현이다. 나머지는 상태 전이가 곧 실행이므로 EXECUTED 다.
# 실제로 끝났는데 ACCEPTED 를 보내면 관제가 완료를 기다리며 멈춘다.
ACK_ACCEPTED = 'ACCEPTED'
ACK_EXECUTED = 'EXECUTED'
ACK_REJECTED = 'REJECTED'
ACK_EXPIRED = 'EXPIRED'
ACK_FAILED = 'FAILED'


TERMINATING_STATES = frozenset(
    {MissionState.POST_RECORDING, MissionState.REPORTING}
)

POST_RECORDING_SECONDS = 3
# 30.5의 "최대 상호작용 시간". 32-5의 MAX_EVENT_SECONDS(300초)와 같은 값을 쓴다.
# 다른 값을 쓰면 한쪽이 먼저 끊어 이벤트가 반쪽이 된다.
MAX_INTERACTION_SECONDS = 300
# 후보가 빈 배열로 이 시간 이어지면 사람을 놓친 것으로 본다.
PERSON_LOST_SECONDS = 3.0


def format_utc(moment: datetime) -> str:
    """`encounter.schema.json`과 `mission-status.schema.json`의 pattern에 맞춘다.

    밀리초까지만 쓰고 반드시 `Z`로 끝난다. 지역 시간대 오프셋을 보내면 백엔드마다
    다르게 해석될 수 있어 스키마가 `Z`를 강제한다.

    노드 파일이 아니라 여기 두는 이유는 CI에서 검증하기 위해서다. 노드는 `rclpy`를
    import하므로 시험이 그것을 가져오면 ROS 없는 컨테이너에서 실패한다.
    `sentinel_recorder`의 `segment_store.format_utc`와 같은 자리다.
    """
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec='milliseconds')
        .replace('+00:00', 'Z')
    )


@dataclass
class Transition:
    """한 번의 입력이 만든 결과.

    상태가 바뀌지 않아도 돌려준다. 무시한 입력을 호출자가 로그로 남길 수 있어야
    한다. S15P11A301-123에서 무시된 encounter가 아무 로그를 남기지 않아 "수신하지
    못한 것"과 "무시한 것"을 구별할 수 없었다.
    """

    changed: bool
    state: MissionState
    previous: MissionState | None = None
    reason: str = ''
    phase: Phase | None = None
    # 이 전이가 가리키는 encounter. `phase` 가 있을 때만 채운다.
    #
    # 호출부(노드)가 발행 시점에 `machine.encounter` 를 다시 읽으면, 전이 도중에
    # encounter 를 버리는 핸들러와 순서가 엉킨다 — 먼저 버리면 `encounterId` 가
    # 빠진 채로 나가고 녹화기는 「진행 중 이벤트가 아니다」로 무시한다
    # (S15P11A301-276). 전이가 대상을 들고 가면 그 위험이 없어지고, 핸들러는
    # 발행 순서를 신경 쓰지 않고 정리할 수 있다.
    encounter: 'Encounter | None' = None
    ignored_reason: str = ''
    # 거부 사유를 기계가 읽을 코드로도 남긴다. `ignored_reason`은 사람이 읽는
    # 문장이고 로그용이다. COMMAND_ACK 의 `reasonCode`가 이 값이며 관제가 그것으로
    # 화면을 분기한다(31-6, S15P11A301-143). 문장을 파싱해 코드를 만들면 문구를
    # 고칠 때마다 관제가 깨진다.
    reason_code: str | None = None


@dataclass
class Encounter:
    """진행 중 encounter. 상태 머신이 소유한다."""

    encounter_id: str
    detected_at: datetime
    person_count: int = 0
    track_ids: set[int] = field(default_factory=set)
    confidence: float | None = None
    # 사람 후보가 마지막으로 보인 시각. 빈 배열이 이어지면 갱신되지 않는다.
    last_seen_at: datetime | None = None
    interaction_started_at: datetime | None = None
    post_recording_started_at: datetime | None = None


class MissionStateMachine:
    """26.3 상태 전이. ROS와 JSON을 모르므로 단독으로 시험할 수 있다."""

    def __init__(
        self,
        *,
        post_recording_seconds: int = POST_RECORDING_SECONDS,
        max_interaction_seconds: int = MAX_INTERACTION_SECONDS,
        person_lost_seconds: float = PERSON_LOST_SECONDS,
        start_state: MissionState = MissionState.SAFE_IDLE,
    ) -> None:
        self.post_recording_seconds = post_recording_seconds
        self.max_interaction_seconds = max_interaction_seconds
        self.person_lost_seconds = person_lost_seconds
        self.state = start_state
        self.encounter: Encounter | None = None
        # 진행 중 임무 식별자. MISSION_START 가 주고 관제가 만든 값이다.
        #
        # 이것이 없으면 발견한 사람이 서버에 기록되지 않는다. 백엔드가
        # encounters.mission_id 를 NOT NULL FK 로 두고 임무 없는 encounter 를
        # 적재하지 않기 때문이다(S15P11A301-138·140).
        self.mission_id: str | None = None
        # 26.4 명령 멱등성. 이미 처리한 commandId는 다시 실행하지 않는다.
        self._handled_commands: set[str] = set()
        # REPORT_COMMITTED가 REPORTING 진입 전에 도착한 encounterId
        # (S15P11A301-160). recorder와 이 머신의 마감 판정 기준이 독립이라
        # recorder가 먼저 끝나는 경합이 있고, 그 신호는 일회성이라 버리면
        # 다시 오지 않는다 — REPORTING에 영구 고착된다. 실기기에서 관측했다.
        self._early_report_committed: set[str] = set()

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    @property
    def movement_allowed(self) -> bool:
        return MOVEMENT[self.state][0]

    @property
    def speed_limit(self) -> float | None:
        return MOVEMENT[self.state][1]

    @property
    def encounter_id(self) -> str | None:
        return self.encounter.encounter_id if self.encounter else None

    @property
    def person_count(self) -> int:
        return self.encounter.person_count if self.encounter else 0

    @property
    def control_mode(self) -> str:
        """자율/수동. `command_mux` 가 이 값으로 명령 소스를 고른다.

        **상태에서 파생되며 독립 필드가 아니다.** 26.3 이 수동 전환을 `PAUSED`
        경유로 정했고 `MANUAL` 이 그 상태이므로, "수동인데 임무가 진행 중" 이라는
        조합은 존재하지 않는다. 그 덕에 수동 주행 중에는 후보를 봐도 encounter 가
        생기지 않는다(`observe_candidates` 가 `EXPLORING` 만 받는다) — 정지·STT·
        녹화가 수동 조종과 뒤엉키는 일이 구조적으로 막힌다.

        어휘는 `state.schema.json` 을 따른다(`MANUAL` 또는 `AUTO`). `cloud_bridge`
        가 관제로 보낼 때 같은 규칙으로 파생하고 있었는데, 규칙이 두 곳에 있으면
        어긋날 때 어느 쪽이 맞는지 알 수 없다. 소유자는 이쪽이다(S15P11A301-278).
        """
        return 'MANUAL' if self.state is MissionState.MANUAL else 'AUTO'

    def is_in_encounter(self) -> bool:
        """encounter 처리 중인 상태인가.

        `REPORTING`을 포함한다. 보고가 끝나기 전에는 encounter가 살아 있어야
        하고, 그 사이에 온 신호가 다음 encounter로 새면 안 된다.
        """
        return self.state in {
            MissionState.PERSON_APPROACHING,
            MissionState.INTERACTING,
            MissionState.POST_RECORDING,
            MissionState.REPORTING,
        }

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _to(
        self,
        state: MissionState,
        reason: str,
        *,
        phase: Phase | None = None,
    ) -> Transition:
        previous = self.state
        self.state = state
        return Transition(
            changed=True,
            state=state,
            previous=previous,
            reason=reason,
            phase=phase,
            # phase 가 있으면 대상 encounter 를 함께 실어 보낸다. 이 뒤에 핸들러가
            # encounter 를 버려도 발행에 필요한 값은 전이가 들고 있다.
            encounter=self.encounter if phase is not None else None,
        )

    def _ignore(self, reason: str, code: str | None = None) -> Transition:
        return Transition(
            changed=False,
            state=self.state,
            ignored_reason=reason,
            reason_code=code,
        )

    def _cut_encounter(self, now: datetime) -> Phase | None:
        """진행 중 encounter 를 마감 신호와 함께 끊는다 (S15P11A301-276).

        `Phase.ENDED` 를 반환하면 호출부가 그것을 전이에 실어 보내고, 노드가
        `/perception/encounter` 로 발행해 녹화기가 `POST_RECORDING` 으로 넘어가
        MP4 를 마감한다. encounter 가 없으면 `None` 을 돌려 아무것도 내지 않는다.

        ## 왜 필요한가

        종전에는 일시정지·종료가 encounter 를 그냥 버리거나 놔뒀고, 마감 신호가
        나가지 않아 녹화기가 5분 `MAX_DURATION` 까지 돌았다. 실측: `REPORTING`
        중에 관제 PAUSE 를 누르자 **92MB** 파일에 `endReason: MAX_DURATION` 이
        남았다. 사람이 없는 구간까지 담겨 증빙 품질도 낮다.

        녹화기가 상태 머신에 의존하지 않는 것은 설계 의도다(상한이 없으면 파일이
        무한히 커진다). 그래서 상한은 옳고 **마감 신호를 제때 주지 않은 쪽이**
        문제였다.

        ## 순서가 중요하다

        `ENDED` 를 만든 **뒤에** 버린다. 노드는 발행 시점에 `machine.encounter_id`
        를 읽으므로(`_apply` → `_publish_encounter`), 먼저 버리면 `encounterId` 가
        빠진 채로 나가고 녹화기는 「진행 중 이벤트가 아니다」로 무시한다.

        그래서 여기서는 **버리지 않는다.** 버리는 것은 호출부 이후의 일이며, 지금은
        `post_recording_started_at` 만 세워 녹화기 쪽 사후 창과 맞춘다
        (`_dialogue_ended` 와 같은 형태다).
        """
        if self.encounter is None:
            return None
        self.encounter.post_recording_started_at = now
        return Phase.ENDED

    # ESTOP 은 여기 부르지 않는다 — 의도된 예외다 (S15P11A301-276).
    #
    # 비상 정지는 무언가 크게 잘못된 순간이고, 그 전후 영상이 길게 남는 것이
    # 오히려 필요하다. 일시정지·종료는 "이 발견을 여기서 끝낸다"는 운영 판단이라
    # 관측 구간만 남기는 것이 맞지만, 비상 정지는 원인 조사 대상이다.
    #
    # 그래서 ESTOP 에서는 encounter 가 살아 있고 녹화가 5분 상한까지 간다.
    # 그것이 이 경로에서는 올바른 동작이다.

    def _clear_encounter(self) -> None:
        """현재 encounter와 그 encounter에 종속된 일회성 표시를 정리한다."""
        if self.encounter is not None:
            self._early_report_committed.discard(
                self.encounter.encounter_id
            )
        self.encounter = None

    # ------------------------------------------------------------------
    # 사람 후보 (25.2·25.4)
    # ------------------------------------------------------------------

    def observe_candidates(
        self,
        *,
        now: datetime,
        track_ids: set[int],
        confidence: float | None = None,
        new_encounter_id: str = '',
    ) -> Transition:
        """확정된 사람 후보를 받는다.

        `new_encounter_id`는 호출자가 미리 만든 UUID다. 상태 머신이 uuid를
        직접 만들면 시험에서 값을 예측할 수 없다. 쓰이지 않으면 버려진다.

        25.4의 중복 제거를 여기서 한다. 활성 encounter가 있으면 새로 만들지 않고
        같은 것에 track을 합친다. 32-6이 "동시에 발견된 사람들은 encounter 하나를
        공유한다"고 정한 이유다.
        """
        if self.state in {MissionState.ESTOP, MissionState.ERROR}:
            return self._ignore(f'{self.state.value} 상태에서는 후보를 받지 않는다')

        if not track_ids:
            return self._observe_absence(now)

        # 활성 encounter가 있으면 합친다. 새 id를 발급하지 않는다.
        if self.encounter is not None:
            return self._merge_into_encounter(now, track_ids, confidence)

        if self.state != MissionState.EXPLORING:
            # SAFE_IDLE이나 PAUSED에서는 사람을 봐도 접근하지 않는다. 26.2가
            # 이동을 허용하지 않는 상태이고, 접근 없이 encounter를 만들면
            # 녹화만 돌다 타임아웃으로 끝난다.
            return self._ignore(
                f'{self.state.value} 상태에서는 새 encounter를 만들지 않는다'
            )

        if not new_encounter_id:
            return self._ignore('encounterId가 주어지지 않았다')

        self.encounter = Encounter(
            encounter_id=new_encounter_id,
            detected_at=now,
            person_count=len(track_ids),
            track_ids=set(track_ids),
            confidence=confidence,
            last_seen_at=now,
        )
        return self._to(
            MissionState.PERSON_APPROACHING,
            'encounter confirmed',
            phase=Phase.CONFIRMED,
        )

    def _merge_into_encounter(
        self, now: datetime, track_ids: set[int], confidence: float | None
    ) -> Transition:
        assert self.encounter is not None
        encounter = self.encounter
        encounter.last_seen_at = now
        if confidence is not None:
            encounter.confidence = max(encounter.confidence or 0.0, confidence)

        fresh = track_ids - encounter.track_ids
        encounter.track_ids |= track_ids
        # personCount는 **한 관측에 동시에 보인** 사람 수의 최대다. 누적 track 수가
        # 아니다.
        #
        # 32-6이 "동시에 발견된 사람들은 encounter 하나를 공유한다"고 정했고 기준은
        # "동시에"다. 누적 집합을 쓰면 한 사람이 들락날락할 때마다 새 trackId가
        # 발급돼 사람 수가 불어난다. 실측에서 사람의 팔 하나가 세 번 들락날락하자
        # personCount가 3이 됐다(S15P11A301-139). 관제에 "3명 발견"으로 보고되면
        # 구조 판단이 틀어진다.
        #
        # 줄이지 않는 것은 유지한다. 한 명이 잠깐 가려도 최대값이 남아야 보고서의
        # "몇 명을 발견했는가"가 흔들리지 않는다.
        #
        # trackId 자체가 갈리는 것은 여기서 고칠 문제가 아니다. 25.4가 "정밀
        # 재식별은 범위에서 제외한다"고 했고 ByteTrack이 붙으면 개선된다.
        encounter.person_count = max(encounter.person_count, len(track_ids))

        # 사후 3초 안에 다시 보이면 상호작용으로 되돌린다(32-5 REDETECTED).
        if self.state == MissionState.POST_RECORDING:
            encounter.post_recording_started_at = None
            encounter.interaction_started_at = now
            return self._to(
                MissionState.INTERACTING,
                'redetected within post recording',
                phase=Phase.REDETECTED,
            )

        if fresh:
            # 사람이 늘었다. `CONFIRMED`를 다시 낸다.
            #
            # 상태는 그대로지만 phase를 내지 않으면 녹화 보고서의 personCount가
            # 처음 값에 멈춘다. 3명을 발견했는데 보고서에 1명으로 남으면 32-6이
            # 요구한 "동시에 발견된 사람들"이 기록에서 사라진다. 검증에서 실제로
            # personCount 1로 굳는 것을 봤다.
            #
            # 같은 encounterId의 반복 CONFIRMED는 안전하다. 녹화 노드가 그것으로
            # 이벤트를 쪼개지 않는다(S15P11A301-123의
            # test_repeated_confirmed_does_not_split_event).
            #
            # `REPORTING`에서는 내지 않는다. 사후 3초가 끝나 녹화 노드가 마감하는
            # 중이므로 새 CONFIRMED가 이벤트를 되살린다.
            phase = (
                Phase.CONFIRMED
                if self.state
                in {MissionState.PERSON_APPROACHING, MissionState.INTERACTING}
                else None
            )
            return Transition(
                changed=False,
                state=self.state,
                reason=f'track {sorted(fresh)} 추가, personCount={encounter.person_count}',
                phase=phase,
            )
        return self._ignore('이미 활성 encounter에 포함된 track이다')

    def _observe_absence(self, now: datetime) -> Transition:
        """후보가 빈 배열로 왔다."""
        if self.encounter is None or not self.is_in_encounter():
            return self._ignore('진행 중 encounter가 없다')

        encounter = self.encounter
        if encounter.last_seen_at is None:
            encounter.last_seen_at = now
            return self._ignore('상실 판정 시작')

        elapsed = (now - encounter.last_seen_at).total_seconds()
        if elapsed < self.person_lost_seconds:
            return self._ignore(f'상실 판정 대기 {elapsed:.1f}초')

        if self.state in TERMINATING_STATES:
            # 이미 종료 절차다. 사람이 없는 것이 정상이므로 다시 끝내지 않는다.
            #
            # `REPORTING`을 빠뜨렸다가 무한 루프를 만들었다(S15P11A301-139).
            #
            #   POST_RECORDING → REPORTING  (3s captured)
            #   REPORTING → POST_RECORDING  (person lost)   ← 되돌아간다
            #   POST_RECORDING → REPORTING  (3s captured)
            #   ...
            #
            # 실물 검증에서 사람이 5~10초 서 있었는데 이벤트가 마감되지 않아 MP4가
            # 46.9초가 됐다. 46프레임 중 사람이 보이는 것은 하나뿐이었다.
            return self._ignore(f'{self.state.value}에서는 상실이 정상이다')

        # 접근 중이나 상호작용 중에 사람을 놓쳤다. 32-5의 LOST다.
        encounter.post_recording_started_at = now
        return self._to(
            MissionState.POST_RECORDING, 'person lost', phase=Phase.LOST
        )

    # ------------------------------------------------------------------
    # 신호 (26.1)
    # ------------------------------------------------------------------

    def handle_signal(
        self,
        signal: Signal,
        *,
        now: datetime,
        encounter_id: str | None = None,
        mission_id: str | None = None,
        command_id: str | None = None,
        detail: str = '',
    ) -> Transition:
        """`/mission/signal`을 처리한다.

        `encounter_id`가 주어졌고 진행 중 것과 다르면 무시한다. 옛 encounter의
        지연된 신호가 새 이벤트를 흔드는 것을 막는다.
        """
        if command_id:
            if command_id in self._handled_commands:
                return self._ignore(
                    f'이미 처리한 commandId {command_id}',
                    REASON_DUPLICATE_COMMAND,
                )
            self._handled_commands.add(command_id)

        # 26.5 우선순위. E-Stop과 센서 결함은 상태와 무관하게 먼저 처리한다.
        if signal is Signal.ESTOP:
            if self.state is MissionState.ESTOP:
                return self._ignore('이미 ESTOP latch 상태다')
            return self._estop(detail)
        if signal is Signal.SENSOR_FAULT:
            return self._sensor_fault(now, detail)

        if self.state in {MissionState.ESTOP, MissionState.ERROR}:
            return self._ignore(
                f'{self.state.value}는 latch 상태다. 운영자 조치가 필요하다',
                REASON_ESTOP_ACTIVE
                if self.state is MissionState.ESTOP
                else REASON_ERROR_LATCHED,
            )

        if encounter_id is not None and encounter_id != self.encounter_id:
            return self._ignore(
                f'encounterId 불일치(신호 {encounter_id[:8]}, '
                f'진행 중 {(self.encounter_id or "없음")[:8]})'
            )

        handler = {
            Signal.MISSION_START: self._mission_start,
            Signal.SAFE_POSE_REACHED: self._safe_pose_reached,
            Signal.APPROACH_FAILED: self._approach_failed,
            Signal.DIALOGUE_ENDED: self._dialogue_ended,
            Signal.REPORT_COMMITTED: self._report_committed,
            Signal.RESUME_REQUESTED: self._resume_requested,
            Signal.PAUSE_REQUESTED: self._pause_requested,
            Signal.RESUME_APPROVED: self._resume_approved,
            Signal.MISSION_COMPLETED: self._mission_completed,
        }[signal]
        if signal is Signal.MISSION_START:
            return self._mission_start(now, detail, mission_id)
        return handler(now, detail)

    def _estop(self, detail: str) -> Transition:
        # encounter를 버리지 않는다. 이미 모은 조각으로 녹화 노드가 이벤트를
        # 마감할 수 있어야 한다(32-5). 다만 phase는 내지 않는다. 녹화 노드는
        # 자기 타임아웃으로 끝낸다.
        return self._to(MissionState.ESTOP, f'ESTOP {detail}'.strip())

    def _sensor_fault(self, now: datetime, detail: str) -> Transition:
        # 26.5는 "핵심 센서 실패는 PAUSED 또는 ERROR"라고만 정했다. 어느 쪽인지는
        # 14.5(장애별 정책)가 정하며 이 티켓에서는 PAUSED로 둔다. 복구 가능한
        # 것을 ERROR로 만들면 운영자가 재개할 방법이 없다.
        if self.state is MissionState.PAUSED:
            return self._ignore('이미 PAUSED 상태다')
        # 관제 일시정지와 같은 이유로 진행 중 encounter 를 끊는다
        # (S15P11A301-276). 센서 실패로 멈추는 것도 그 발견을 이어갈 수 없는
        # 상황이며, 마감 신호를 내지 않으면 녹화가 5분 상한까지 돈다.
        #
        # 이 경로를 처음에 빠뜨렸다. PAUSED 로 가는 핸들러가 둘인 것을 뮤테이션
        # 시험에서 발견했다 — 관제 PAUSE 만 고치고 센서 실패는 그대로였다.
        phase = self._cut_encounter(now)
        return self._to(
            MissionState.PAUSED,
            f'SENSOR_FAULT {detail}'.strip(),
            phase=phase,
        )

    def _mission_start(
        self, now: datetime, detail: str, mission_id: str | None = None
    ) -> Transition:
        """임무를 시작한다. missionId를 보관해 이후 encounter에 담는다.

        `missionId`가 없어도 시작은 허용한다. 개발 중에는 관제 없이 젯슨만 띄워
        녹화를 검증하는 일이 잦고, 그때 임무를 만들 수단이 없다. 대신 그 상태에서
        발행한 encounter는 서버에 기록되지 않으므로 호출자가 경고를 남긴다.
        """
        if self.state is MissionState.EXPLORING:
            # 이미 탐사 중이다. 관제 버튼을 두 번 눌렀거나, 서로 다른 commandId로
            # 두 번 온 경우다(멱등 가드는 같은 commandId만 막는다). 원하는 상태에
            # 이미 있으므로 거부로 보지 않는다 — 조작자에게는 성공이 맞다.
            return self._ignore('이미 EXPLORING 상태다')
        # COMPLETED 에서도 시작을 허용한다 (S15P11A301-274).
        #
        # 종전에는 SAFE_IDLE 만 허용했고 COMPLETED 는 나가는 전이가 없는 종단이라,
        # 관제에서 임무를 한 번 종료하면 mission_manager 를 재기동하지 않는 한 다시
        # 시작할 수 없었다. 시연은 반복하므로 STOP 한 번이 일회용 잠금이 됐다.
        #
        # 초기화 버튼을 따로 두지 않는 이유는 **임무 종료 기록이 상태 머신에 있지
        # 않기 때문**이다. personCount 는 encounter 에서 파생되고 missionId 는 관제가
        # 준 값이며, 완료 이력은 관제 DB 의 임무 이력에 남는다. 살아 있는 상태가
        # COMPLETED 를 붙들고 있어야 이력이 보존되는 것이 아니다. 조작자는 이미
        # 「탐사 시작」으로 의도를 표현했으므로 클릭을 하나 더 요구하지 않는다.
        if self.state not in {MissionState.SAFE_IDLE, MissionState.COMPLETED}:
            return self._ignore(
                'MISSION_START는 SAFE_IDLE·COMPLETED에서만 유효하다'
                f'(현재 {self.state.value})',
                REASON_INVALID_STATE,
            )

        # 여기서 _clear_encounter() 를 부르지 않는다. 부르는 코드를 넣었다가
        # 뮤테이션 시험에서 **죽은 코드임이 드러나 지웠다** — 허용된 두 진입 상태
        # 모두 encounter 가 이미 없다. SAFE_IDLE 은 초기 상태이고, COMPLETED 는
        # _mission_completed 가 진행 중 encounter 와 mission_id 를 지우고 오기
        # 때문이다.
        #
        # 새 임무가 이전 임무의 personCount·encounterId 를 물려받지 않는다는 것은
        # 시험으로 고정해 두었다(test_새_임무는_이전_임무의_encounter를_물려받지_않는다).
        # 지우는 주체가 어디든 그 불변식이 깨지면 시험이 잡는다.
        self.mission_id = mission_id
        return self._to(MissionState.EXPLORING, 'MISSION_START')

    def _mission_completed(self, now: datetime, detail: str) -> Transition:
        """관제의 STOP. 26.3의 COMPLETED로 보낸다 (S15P11A301-143).

        어느 상태에서든 받는다. 조작자가 임무를 끝내겠다고 했으면 탐사 중이든
        상호작용 중이든 끝나야 한다. 23.4가 "사용자 종료"를 종료 조건으로 명시했다.

        진행 중 encounter는 버린다. 상호작용이 끝나지 않은 상태로 임무가 끝나므로
        그 발견을 완결된 것으로 보고하면 잘못된 기록이 된다. 이미 마감된 encounter는
        recording_manager가 별도로 처리했으므로 영향받지 않는다.

        `mission_id`는 지운다. 임무가 끝났으므로 이후 encounter가 그 임무에 붙으면
        안 된다. 백엔드도 종료된 임무의 명령을 MISSION_ALREADY_ENDED로 거부한다.

        latch 상태(ESTOP·ERROR)는 여기 오지 않는다. `handle_signal`이 먼저
        걸러낸다 — 비상 정지를 STOP으로 풀 수는 없고, 그것은 운영자가 물리적으로
        확인한 뒤 해제할 일이다(26.5).
        """
        if self.state is MissionState.COMPLETED:
            return self._ignore('이미 COMPLETED 상태다')
        phase = self._cut_encounter(now)
        transition = self._to(
            MissionState.COMPLETED,
            f'MISSION_COMPLETED {detail}'.strip(),
            phase=phase,
        )
        # 전이가 encounter 를 들고 가므로 여기서 버려도 발행이 깨지지 않는다.
        # 임무가 끝났으니 다음 임무로 새지 않아야 한다.
        self._clear_encounter()
        return transition

    def _safe_pose_reached(self, now: datetime, detail: str) -> Transition:
        if self.state is not MissionState.PERSON_APPROACHING:
            return self._ignore(
                f'SAFE_POSE_REACHED는 PERSON_APPROACHING에서만 유효하다'
                f'(현재 {self.state.value})'
            )
        if self.encounter is not None:
            self.encounter.interaction_started_at = now
        return self._to(
            MissionState.INTERACTING, 'safe pose reached', phase=Phase.APPROACHED
        )

    def _approach_failed(self, now: datetime, detail: str) -> Transition:
        """30.3: 접근이 불가능하면 현재 안전 위치에서 음성을 송출한다.

        사람을 향해 무리하게 직진하지 않는다. 그래서 상호작용으로는 넘어가되
        접근했다는 사실은 남기지 않는다.
        """
        if self.state is not MissionState.PERSON_APPROACHING:
            return self._ignore(
                f'APPROACH_FAILED는 PERSON_APPROACHING에서만 유효하다'
                f'(현재 {self.state.value})'
            )
        if self.encounter is not None:
            self.encounter.interaction_started_at = now
        return self._to(
            MissionState.INTERACTING,
            f'APPROACH_FAILED {detail}'.strip(),
            phase=Phase.APPROACHED,
        )

    def _dialogue_ended(self, now: datetime, detail: str) -> Transition:
        if self.state is not MissionState.INTERACTING:
            return self._ignore(
                f'DIALOGUE_ENDED는 INTERACTING에서만 유효하다'
                f'(현재 {self.state.value})'
            )
        if self.encounter is not None:
            self.encounter.post_recording_started_at = now
        return self._to(
            MissionState.POST_RECORDING, 'dialogue ended', phase=Phase.ENDED
        )

    def _report_committed(self, now: datetime, detail: str) -> Transition:
        """recorder의 보고 저장 완료. REPORTING이면 즉시 탐사로 복귀한다.

        **이르게 도착하면 버리지 않고 기억한다** (S15P11A301-160). recorder와 이
        머신의 마감 판정 기준이 독립이라(각자의 타임아웃·소실 판정) recorder가
        먼저 마감하고 이 머신은 아직 INTERACTING인 경합이 있다. 신호는 일회성이고
        recorder는 마감한 encounter를 다시 다루지 않으므로(S15P11A301-142),
        여기서 버리면 REPORTING 진입 후 기다릴 것이 영영 오지 않는다. 실기기에서
        66초 만에 고착으로 관측했다.
        """
        if self.state is not MissionState.REPORTING:
            early_states = {
                MissionState.INTERACTING,
                MissionState.POST_RECORDING,
            }
            if self.encounter is not None and self.state in early_states:
                self._early_report_committed.add(self.encounter.encounter_id)
                return self._ignore(
                    f'{self.state.value}에 이르게 도착했다. 기억해 두고 '
                    'REPORTING 진입 시 적용한다 (S15P11A301-160)'
                )
            return self._ignore(
                f'REPORT_COMMITTED는 REPORTING에서만 유효하다'
                f'(현재 {self.state.value})'
            )
        # 26.3: REPORTING → EXPLORING: report committed
        self._clear_encounter()
        return self._to(MissionState.EXPLORING, 'report committed')

    def _resume_requested(self, now: datetime, detail: str) -> Transition:
        """음성 쪽의 재개 요청(report_lifecycle의 request_exploration_resume).

        30.5가 "안전 장애·미디어 저장 실패·Mission Manager 오류가 있으면 자동
        재개하지 않고 PAUSED를 유지한다"고 정했다. 그래서 PAUSED에서 온 요청은
        받지 않는다. 운영자의 명시적 RESUME_APPROVED만 PAUSED를 풀 수 있다.
        """
        if self.state is MissionState.PAUSED:
            return self._ignore(
                'PAUSED는 자동 재개하지 않는다(30.5). 운영자 재개가 필요하다'
            )
        if self.state is MissionState.REPORTING:
            self._clear_encounter()
            return self._to(MissionState.EXPLORING, 'RESUME_REQUESTED')
        return self._ignore(
            f'RESUME_REQUESTED는 REPORTING에서만 유효하다(현재 {self.state.value})'
        )

    def _pause_requested(self, now: datetime, detail: str) -> Transition:
        if self.state is MissionState.PAUSED:
            return self._ignore('이미 PAUSED 상태다')
        phase = self._cut_encounter(now)
        return self._to(
            MissionState.PAUSED,
            f'PAUSE_REQUESTED {detail}'.strip(),
            phase=phase,
        )

    def _resume_approved(self, now: datetime, detail: str) -> Transition:
        if self.state is not MissionState.PAUSED:
            return self._ignore(
                f'RESUME_APPROVED는 PAUSED에서만 유효하다(현재 {self.state.value})',
                REASON_INVALID_STATE,
            )
        # 26.3: PAUSED → EXPLORING: explicit resume.
        # 진행 중이던 encounter는 버린다. 일시정지 사이에 상황이 바뀌었을 수 있고,
        # 옛 encounter로 상호작용을 이어가면 잘못된 보고가 된다.
        self._clear_encounter()
        return self._to(MissionState.EXPLORING, 'RESUME_APPROVED')

    # ------------------------------------------------------------------
    # 시간 경과
    # ------------------------------------------------------------------

    def tick(self, now: datetime) -> Transition:
        """시간으로만 일어나는 전이를 처리한다.

        `POST_RECORDING` 3초 경과와 최대 상호작용 시간이다. 주기적으로 불러야
        하며, 부르지 않으면 이벤트가 끝나지 않는다.
        """
        encounter = self.encounter

        if self.state is MissionState.POST_RECORDING and encounter is not None:
            started = encounter.post_recording_started_at
            if started is not None:
                elapsed = (now - started).total_seconds()
                if elapsed >= self.post_recording_seconds:
                    # 보고가 이미 저장됐으면 REPORTING에서 기다릴 것이 없다.
                    # 기다리면 그 신호는 다시 오지 않아 영구 고착이다
                    # (S15P11A301-160).
                    if encounter.encounter_id in self._early_report_committed:
                        self._clear_encounter()
                        return self._to(
                            MissionState.EXPLORING,
                            'report committed (REPORTING 진입 전 수신)',
                        )
                    # 26.3: POST_RECORDING → REPORTING: 3s captured
                    return self._to(MissionState.REPORTING, '3s captured')
            return self._ignore('사후 녹화 진행 중')

        if self.state is MissionState.INTERACTING and encounter is not None:
            started = encounter.interaction_started_at
            if started is not None:
                elapsed = (now - started).total_seconds()
                if elapsed >= self.max_interaction_seconds:
                    # 30.5의 "최대 상호작용 시간이 지났다".
                    encounter.post_recording_started_at = now
                    return self._to(
                        MissionState.POST_RECORDING,
                        'max interaction time',
                        phase=Phase.ENDED,
                    )
            return self._ignore('상호작용 진행 중')

        return self._ignore(f'{self.state.value}에는 시간 전이가 없다')

    def deadline_hint(self) -> datetime | None:
        """다음 시간 전이 시각. 없으면 None이다.

        호출자가 타이머 주기를 정하는 데 쓴다. `sentinel_recorder`의 상태 머신과
        같은 목적이다.
        """
        encounter = self.encounter
        if encounter is None:
            return None
        if self.state is MissionState.POST_RECORDING:
            started = encounter.post_recording_started_at
            if started is not None:
                return started + timedelta(seconds=self.post_recording_seconds)
        if self.state is MissionState.INTERACTING:
            started = encounter.interaction_started_at
            if started is not None:
                return started + timedelta(seconds=self.max_interaction_seconds)
        return None

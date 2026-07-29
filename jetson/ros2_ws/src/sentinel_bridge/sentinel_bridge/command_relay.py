"""관제 명령을 신호로 바꿀지 거부할지 결정한다 (S15P11A301-143, 명세 31-4·31-6).

`cloud_bridge_node`에서 판단 부분만 떼어 놓았다. ROS도 MQTT도 모르므로 CI에서
시험할 수 있다. `message_mapper`와 `mqtt_client`를 그렇게 둔 것과 같은 이유다.

## 왜 떼어냈는가

중복 명령 처리가 조용히 틀리기 쉬운 자리다. QoS 1은 같은 메시지를 두 번 준다
(31-4). 그때 신호를 다시 넣으면 `mission_manager`의 멱등 가드가
`DUPLICATE_COMMAND`로 거부하고, 그 거부를 ACK로 보내면 백엔드가 이미 `EXECUTED`로
기록한 명령을 `REJECTED`로 덮어쓴다. 관제 화면에는 "명령이 거부됨"이 뜨는데 로봇은
실제로 그 명령을 수행한 상태다.

이 결함은 브로커가 재전송할 때만 드러난다. 그리고 브로커 ACL이 로봇 계정의
`cmd/*` 발행을 막으므로(옳은 설정이다) 젯슨에서 재전송을 인위적으로 만들 수 없다.
즉 **실물 검증으로는 이 경로를 확인할 방법이 없다.** 그래서 판단을 순수 함수로
떼어 시험으로 고정한다.

## 판단은 최소로 한다

명령이 지금 상태에서 유효한지는 `mission_manager`가 정한다(26.1 단일 권한).
여기서 거부하는 것은 상태와 무관한 사유뿐이다.

    형식이 틀렸다          MALFORMED_COMMAND
    매핑이 없다(RETURN)     NOT_IMPLEMENTED
    mission_manager 없음    MISSION_MANAGER_DOWN

`commandId`가 없으면 회신할 대상을 특정할 수 없으므로 ACK도 못 보내고 버린다.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from .message_mapper import COMMAND_TO_SIGNAL, MESSAGE_TYPE_MISSION_COMMAND

# ACK 캐시 상한. 명령은 조작자가 누를 때만 오므로 임무 하나에서 수십 건이다.
# 무한히 쌓아 두면 긴 임무에서 메모리가 늘고, 아주 오래된 commandId가 다시 올 일은
# 없다. FIFO로 밀어낸다.
ACK_CACHE_SIZE = 256

ACK_REJECTED = 'REJECTED'

REASON_NOT_IMPLEMENTED = 'NOT_IMPLEMENTED'
REASON_MISSION_MANAGER_DOWN = 'MISSION_MANAGER_DOWN'
REASON_MALFORMED_COMMAND = 'MALFORMED_COMMAND'


@dataclass
class Decision:
    """명령 하나에 대한 결정.

    셋 중 하나다.

        signal 있음   `/mission/signal`로 그 신호를 낸다. ACK는 결과가 오면 낸다
        ack 있음      즉시 그 ACK를 낸다. 신호는 내지 않는다
        둘 다 없음     버린다. 회신할 수 없거나 처리 중인 중복이다
    """

    signal: str | None = None
    ack: dict[str, Any] | None = None
    command_id: str | None = None
    mission_id: str | None = None
    command_type: str | None = None
    # 사람이 읽을 판단 근거. 호출자가 로그로 남긴다. 조용히 버리면 "수신하지 못한
    # 것"과 "버린 것"을 구별할 수 없다(S15P11A301-123에서 겪었다).
    note: str = ''
    # 이미 회신한 ACK를 다시 보내는 경우. 로그 문구를 가르는 데 쓴다.
    replayed: bool = False


class CommandRelay:
    """명령을 신호로 바꿀지 거부할지 결정하고, 회신한 ACK를 기억한다."""

    def __init__(self, ack_cache_size: int = ACK_CACHE_SIZE) -> None:
        self._ack_cache_size = ack_cache_size
        # commandId → 이미 회신한 ACK 본문
        self._acks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        # 신호는 넣었고 결과는 아직인 명령
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def ack_for(self, command_id: str) -> dict[str, Any] | None:
        return self._acks.get(command_id)

    # ------------------------------------------------------------------
    # 결정
    # ------------------------------------------------------------------

    def decide(
        self, envelope: dict[str, Any], *, mission_manager_alive: bool
    ) -> Decision:
        """`cmd/mission` 봉투 하나를 보고 무엇을 할지 정한다."""
        message_type = envelope.get('messageType')
        if message_type != MESSAGE_TYPE_MISSION_COMMAND:
            return Decision(
                note=f'cmd/mission에 예상과 다른 messageType: {message_type}'
            )

        data = envelope.get('data')
        if not isinstance(data, dict):
            return Decision(note='cmd/mission 본문이 객체가 아니다')

        command_id = data.get('commandId')
        command_type = data.get('type')
        if not isinstance(command_id, str) or not command_id:
            # 회신할 대상을 특정할 수 없다. ACK도 못 보낸다.
            return Decision(
                note=f'commandId 없는 명령을 버렸다: type={command_type!r}'
            )

        mission_id = envelope.get('missionId')
        mission_id = mission_id if isinstance(mission_id, str) else None
        base = {
            'command_id': command_id,
            'mission_id': mission_id,
            'command_type': command_type if isinstance(command_type, str) else None,
        }

        # 이미 회신한 명령이면 **같은 ACK를 그대로 다시 보낸다.**
        #
        # 여기서 신호를 다시 넣으면 mission_manager가 DUPLICATE_COMMAND로 거부하고,
        # 그 거부가 백엔드의 EXECUTED 기록을 REJECTED로 덮어쓴다. 재전송에 같은
        # 응답을 주는 것이 멱등의 정의다.
        cached = self._acks.get(command_id)
        if cached is not None:
            return Decision(
                ack=cached,
                replayed=True,
                note=f'중복 명령 {command_id[:8]}. 같은 ACK를 다시 보낸다',
                **base,
            )

        # 신호는 넣었고 결과는 아직인 창에서 온 중복이다. 버린다 — 결과가 오면
        # 그때 ACK가 한 번 나간다.
        if command_id in self._inflight:
            return Decision(
                note=f'처리 중인 명령 {command_id[:8]}의 중복이다. 버린다',
                **base,
            )

        if not isinstance(command_type, str):
            return Decision(
                ack=self._reject(
                    command_id,
                    REASON_MALFORMED_COMMAND,
                    f'type이 문자열이 아니다: {command_type!r}',
                ),
                note=f'형식이 틀린 명령 {command_id[:8]}',
                **base,
            )

        signal = COMMAND_TO_SIGNAL.get(command_type)
        if signal is None:
            # RETURN이 여기 온다. 계약에는 있으나 RETURNING이 미구현이다.
            # 조용히 무시하면 관제가 영원히 PENDING을 본다.
            return Decision(
                ack=self._reject(
                    command_id,
                    REASON_NOT_IMPLEMENTED,
                    f'{command_type}은 아직 구현하지 않았다',
                ),
                note=f'{command_type} 미구현',
                **base,
            )

        if not mission_manager_alive:
            # 신호를 넣어도 받을 노드가 없다. 넣고 기다리면 ACK가 오지 않는다.
            return Decision(
                ack=self._reject(
                    command_id,
                    REASON_MISSION_MANAGER_DOWN,
                    'mission_manager가 실행 중이 아니다',
                ),
                note='mission_manager 없음',
                **base,
            )

        self._inflight.add(command_id)
        return Decision(
            signal=signal,
            note=f'{command_type} → {signal}',
            **base,
        )

    # ------------------------------------------------------------------
    # 결과 수신
    # ------------------------------------------------------------------

    def resolve(self, body: dict[str, Any]) -> str | None:
        """`mission_manager`의 결과를 기록한다. `commandId`를 돌려준다.

        회신할 수 없는 본문이면 None이다.
        """
        command_id = body.get('commandId')
        if not isinstance(command_id, str) or not command_id:
            return None
        self._inflight.discard(command_id)
        self._remember(command_id, body)
        return command_id

    def _reject(
        self, command_id: str, code: str, detail: str
    ) -> dict[str, Any]:
        body = {
            'commandId': command_id,
            'status': ACK_REJECTED,
            'reasonCode': code,
            'message': detail,
        }
        self._remember(command_id, body)
        return body

    def _remember(self, command_id: str, body: dict[str, Any]) -> None:
        self._acks[command_id] = body
        self._acks.move_to_end(command_id)
        while len(self._acks) > self._ack_cache_size:
            self._acks.popitem(last=False)

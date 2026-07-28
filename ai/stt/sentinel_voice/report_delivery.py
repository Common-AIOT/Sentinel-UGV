"""음성 세션 보고의 전송 대기 계약.

실제 MQTT 발행과 SQLite Outbox 적재는 sentinel_bridge가 담당한다. 이 모듈은
음성 파이프라인이 팀원 모듈에 직접 의존하지 않고 넘길 수 있는 이벤트와 상태만 정의한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .guide_audio import GuideCode


class DeliveryState(str, Enum):
    """관제 보고 전달 상태."""

    PENDING = "PENDING"  # bridge 또는 Outbox에 인계 대기
    QUEUED = "QUEUED"  # bridge/Outbox가 인수함(서버 수신 성공은 아님)
    SUCCEEDED = "SUCCEEDED"  # 관제 ACK 확인
    FAILED = "FAILED"  # 인계 자체가 실패함


@dataclass(frozen=True)
class DeliveryResult:
    state: DeliveryState
    guide_code: GuideCode
    detail: str = ""


def queue_report(
    report: dict[str, Any],
    enqueue: Callable[[dict[str, Any]], bool] | None = None,
) -> DeliveryResult:
    """보고서를 bridge에 인계한다.

    ``enqueue``가 없거나 실패해도 성공 안내를 하지 않는다. QUEUED 역시 서버 ACK가
    아니므로 REPORT_PENDING을 사용한다. S15P11A301-116에서 ACK 이벤트와 연결한다.
    """

    if enqueue is None:
        return DeliveryResult(
            DeliveryState.PENDING,
            GuideCode.REPORT_PENDING,
            "관제 전송 어댑터 미연결",
        )
    try:
        accepted = bool(enqueue(report))
    except Exception as error:
        return DeliveryResult(
            DeliveryState.FAILED,
            GuideCode.NETWORK_WAIT,
            f"전송 대기열 인계 실패: {type(error).__name__}",
        )
    if accepted:
        return DeliveryResult(
            DeliveryState.QUEUED,
            GuideCode.REPORT_PENDING,
            "전송 대기열 인계 완료, 관제 ACK 대기",
        )
    return DeliveryResult(
        DeliveryState.FAILED,
        GuideCode.NETWORK_WAIT,
        "전송 대기열이 보고서를 인수하지 않음",
    )

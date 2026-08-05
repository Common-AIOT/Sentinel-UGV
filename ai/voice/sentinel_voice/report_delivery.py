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

    종료 안내는 발신 상태와 무관하게 단일 문구(REPORT_SUCCEEDED_DEPARTURE)다.
    2026-08-01 팀 결정(S15P11A301-146 v2) — 로봇 다수 투입으로 관제 ACK가 없고,
    브리지는 로컬 프로세스라 인계 실패는 없다고 가정한다. 실패가 나면 요구조자는
    완료 안내를 듣지만 보고는 나가지 않는다 — 이 잔여 위험은 상태·로그·세션
    기록으로만 남는다(문서 §11 알려진 한계). 탐사 재개를 약속할 수 있는지는
    임무 상태를 아는 호출자가 판단한다.
    """

    if enqueue is None:
        return DeliveryResult(
            DeliveryState.PENDING,
            GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            "관제 전송 어댑터 미연결",
        )
    try:
        accepted = bool(enqueue(report))
    except Exception as error:
        return DeliveryResult(
            DeliveryState.FAILED,
            GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            f"전송 대기열 인계 실패: {type(error).__name__}",
        )
    if accepted:
        return DeliveryResult(
            DeliveryState.QUEUED,
            GuideCode.REPORT_SUCCEEDED_DEPARTURE,
            "전송 대기열 인계 완료",
        )
    return DeliveryResult(
        DeliveryState.FAILED,
        GuideCode.REPORT_SUCCEEDED_DEPARTURE,
        "전송 대기열이 보고서를 인수하지 않음",
    )

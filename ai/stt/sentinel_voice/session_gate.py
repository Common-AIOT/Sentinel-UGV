"""VISION 트리거 직후 음성 세션 시작 여부를 결정한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from . import config
from .gms_resilience import probe_gms_endpoint
from .guide_audio import GuideCode


class SessionGateState(str, Enum):
    """음성 세션 시작 판정."""

    READY = "READY"  # GMS 호스트 도달 가능, VAD/STT 진행
    GMS_UNAVAILABLE = "GMS_UNAVAILABLE"  # 신규 STT 세션을 시작하지 않음
    GMS_MISCONFIGURED = "GMS_MISCONFIGURED"  # 키가 없어 운영자 조치 필요


@dataclass(frozen=True)
class SessionGateResult:
    state: SessionGateState
    proceed: bool
    guide_code: GuideCode | None
    operator_review_required: bool


def check_session_gate(
    probe: Callable[[str], bool] | None = None,
) -> SessionGateResult:
    """GMS 설정과 실제 GMS 호스트 도달성을 기준으로 세션 시작을 결정한다."""

    # 차단 시 안내 문구는 없다. NETWORK_WAIT은 146 v2에서 삭제됐고("연결되는
    # 대로 전달하겠습니다"는 세션 데이터가 없어 지킬 수 없는 약속이었다),
    # 차단 사실은 로그와 operator_review_required로 남는다.
    if not config.GMS_KEY:
        return SessionGateResult(
            SessionGateState.GMS_MISCONFIGURED,
            False,
            None,
            True,
        )

    if probe is None:
        probe = lambda base_url: probe_gms_endpoint(
            base_url, timeout_seconds=config.GMS_PROBE_TIMEOUT
        )
    if not probe(config.GMS_BASE_URL):
        return SessionGateResult(
            SessionGateState.GMS_UNAVAILABLE,
            False,
            None,
            True,
        )
    return SessionGateResult(SessionGateState.READY, True, None, False)

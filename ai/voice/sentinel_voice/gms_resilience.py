"""GMS 장애 분류와 제한 재시도 정책.

네트워크 단절, 인증/설정 오류, 할당량 제한, 서버 오류를 구분한다.
이 구분은 운영 로그와 관제 보고용이며 의료 판단에는 사용하지 않는다.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlparse


class GmsFailureKind(str, Enum):
    """GMS 호출 실패 원인."""

    DEPENDENCY = "DEPENDENCY"  # openai SDK 등 실행 의존성 누락
    NETWORK = "NETWORK"  # DNS, 연결 거부 등 GMS에 도달하지 못함
    TIMEOUT = "TIMEOUT"  # 설정된 제한 시간 안에 응답하지 않음
    AUTH = "AUTH"  # API 키 누락 또는 HTTP 401/403
    RATE_LIMIT = "RATE_LIMIT"  # HTTP 429
    SERVER = "SERVER"  # HTTP 5xx
    INVALID_RESPONSE = "INVALID_RESPONSE"  # JSON/응답 계약 오류
    CLIENT = "CLIENT"  # 그 밖의 HTTP 4xx 또는 호출 오류


@dataclass(frozen=True)
class GmsFailure:
    """비밀정보를 제외하고 공유 가능한 GMS 실패 요약."""

    kind: GmsFailureKind
    retryable: bool
    status_code: int | None
    error_type: str


@dataclass(frozen=True)
class GmsCallResult:
    """GMS 또는 키워드 폴백 결과와 장애 증적."""

    extraction: dict[str, Any]
    source: str
    attempts: int
    failure: GmsFailure | None = None


def _status_code(error: Exception) -> int | None:
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def classify_gms_error(error: Exception) -> GmsFailure:
    """OpenAI SDK 버전에 의존하지 않고 예외명과 HTTP 상태로 장애를 분류한다."""

    status = _status_code(error)
    name = type(error).__name__
    lowered = name.lower()

    if isinstance(error, ModuleNotFoundError):
        kind = GmsFailureKind.DEPENDENCY
    elif status in {401, 403} or "authentication" in lowered or "permission" in lowered:
        kind = GmsFailureKind.AUTH
    elif status == 429 or "ratelimit" in lowered:
        kind = GmsFailureKind.RATE_LIMIT
    elif status is not None and 500 <= status <= 599:
        kind = GmsFailureKind.SERVER
    elif isinstance(error, (TimeoutError, socket.timeout)) or "timeout" in lowered:
        kind = GmsFailureKind.TIMEOUT
    elif isinstance(error, (ConnectionError, OSError)) or "connection" in lowered:
        kind = GmsFailureKind.NETWORK
    elif isinstance(error, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        kind = GmsFailureKind.INVALID_RESPONSE
    else:
        kind = GmsFailureKind.CLIENT

    retryable = kind in {
        GmsFailureKind.NETWORK,
        GmsFailureKind.TIMEOUT,
        GmsFailureKind.RATE_LIMIT,
        GmsFailureKind.SERVER,
    }
    return GmsFailure(kind, retryable, status, name)


def call_with_limited_retry(
    operation: Callable[[], dict[str, Any]],
    *,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | None, int, GmsFailure | None]:
    """일시 장애만 최대 ``max_attempts``까지 호출한다.

    인증 실패와 응답 계약 오류는 반복해도 회복되지 않으므로 즉시 중단한다.
    """

    attempts = 0
    last_failure = None
    while attempts < max(1, max_attempts):
        attempts += 1
        try:
            return operation(), attempts, None
        except Exception as error:
            last_failure = classify_gms_error(error)
            if not last_failure.retryable or attempts >= max(1, max_attempts):
                break
            sleeper(max(0.0, retry_delay_seconds))
    return None, attempts, last_failure


def probe_gms_endpoint(base_url: str, *, timeout_seconds: float = 2.0) -> bool:
    """신규 세션 전에 GMS 호스트까지 TCP 연결 가능한지만 확인한다.

    일반 인터넷 사이트가 아니라 실제 GMS 호스트를 검사한다. API 요청을 보내지 않으므로
    크레딧을 소비하지 않으며, 성공 여부는 이후 실제 호출 결과를 대신하지 않는다.
    """

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False

"""GMS 연결·실호출·장애 대응 스모크 검사.

개인정보가 없는 고정 합성 문장만 사용하며 GMS 키와 인증 헤더는 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from sentinel_voice import config
from sentinel_voice.gms_resilience import (
    GmsFailureKind,
    call_with_limited_retry,
    probe_gms_endpoint,
)
from sentinel_voice.llm import extract_with_status
from sentinel_voice.safety import EXTRACTION_FIELDS


SYNTHETIC_TEXT = "주변에 세 명이 있고 저는 움직일 수 없어요. 숨쉬기가 어렵습니다."


class SimulatedHttpError(Exception):
    """실제 외부 장애 없이 HTTP 상태 분류를 검사하기 위한 예외."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"simulated HTTP {status_code}")
        self.status_code = status_code


def _simulate(error: Exception, max_attempts: int = 2) -> dict[str, Any]:
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise error

    _, attempts, failure = call_with_limited_retry(
        fail,
        max_attempts=max_attempts,
        retry_delay_seconds=0,
        sleeper=lambda _: None,
    )
    return {
        "kind": failure.kind.value,
        "retryable": failure.retryable,
        "attempts": attempts,
        "statusCode": failure.status_code,
    }


def run_fault_checks() -> dict[str, Any]:
    """401·429·503·timeout 정책을 외부 요청 없이 검사한다."""

    checks = {
        "auth401": _simulate(SimulatedHttpError(401)),
        "rateLimit429": _simulate(SimulatedHttpError(429)),
        "server503": _simulate(SimulatedHttpError(503)),
        "timeout": _simulate(TimeoutError()),
    }
    expected = {
        "auth401": (GmsFailureKind.AUTH.value, 1),
        "rateLimit429": (GmsFailureKind.RATE_LIMIT.value, 2),
        "server503": (GmsFailureKind.SERVER.value, 2),
        "timeout": (GmsFailureKind.TIMEOUT.value, 2),
    }
    for name, (kind, attempts) in expected.items():
        row = checks[name]
        row["passed"] = row["kind"] == kind and row["attempts"] == attempts
    return checks


def run_live_check() -> dict[str, Any]:
    """고정 합성 문장으로 GMS를 한 번 검증한다."""

    if not config.GMS_KEY:
        return {
            "executed": False,
            "passed": False,
            "reason": "GMS_KEY_NOT_CONFIGURED",
        }

    started = time.monotonic()
    result = extract_with_status(SYNTHETIC_TEXT)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    schema_valid = set(result.extraction) == set(EXTRACTION_FIELDS)
    return {
        "executed": True,
        "passed": result.source == "GMS" and schema_valid,
        "model": config.LLM_MODEL,
        "source": result.source,
        "attempts": result.attempts,
        "elapsedMs": elapsed_ms,
        "schemaValid": schema_valid,
        "extraction": result.extraction,
        "failureKind": result.failure.kind.value if result.failure else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="고정 합성 문장으로 실제 GMS API를 호출한다.",
    )
    parser.add_argument("--report", type=Path, help="비밀값 없는 JSON 결과 저장 경로")
    args = parser.parse_args()

    report = {
        "gmsHostReachable": probe_gms_endpoint(
            config.GMS_BASE_URL,
            timeout_seconds=config.GMS_PROBE_TIMEOUT,
        ),
        "keyConfigured": bool(config.GMS_KEY),
        "faultChecks": run_fault_checks(),
        "live": run_live_check() if args.live else {"executed": False},
    }
    passed = (
        report["gmsHostReachable"]
        and all(row["passed"] for row in report["faultChecks"].values())
        and (not args.live or report["live"]["passed"])
    )
    report["passed"] = passed

    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""음성 세션 보고값을 33-6 계약에 맞게 검증하고 보정한다."""

from __future__ import annotations

from typing import Any


REPORT_FIELDS = (
    "responseScope",  # 발화 적용 범위: 단일 마이크이므로 그룹 단위
    "anyResponseDetected",  # 세션 중 사람의 음성 응답 감지 여부
    "reportedResponsiveCount",  # 화자 본인을 포함해 직접 보고한 응답 가능 총인원
    "reportedCountStatus",  # 인원 수의 출처·확정 상태
    "countConfidence",  # 인원 수 인식 신뢰도(측정값이 있을 때만 사용)
    "mobilityStatus",  # 그룹이 스스로 이동할 수 있다고 답했는지
    "urgentConditionReported",  # 심한 출혈·호흡 곤란 등 긴급 상태 언급 여부
    "operatorReviewRequired",  # 관제 담당자의 최종 확인 필요 여부
    "terminationReason",  # 음성 세션이 종료된 이유
)

EXTRACTION_FIELDS = (
    "reportedResponsiveCount",  # GMS/폴백이 발화에서 추출한 인원 수
    "mobilityStatus",  # GMS/폴백이 발화에서 추출한 이동 가능 여부
    "urgentConditionReported",  # GMS/폴백이 발화에서 추출한 긴급 언급 여부
)

ENUMS = {
    "responseScope": {"GROUP"},
    "reportedCountStatus": {
        "SELF_REPORTED_GROUP_COUNT",
        "CONFIRMED_BY_OPERATOR",
        "UNKNOWN",
    },
    "mobilityStatus": {"YES", "NO", "UNKNOWN"},
    "urgentConditionReported": {"YES", "NO", "UNKNOWN"},
    "terminationReason": {
        "NORMAL",
        "TIMEOUT",
        "ABORTED_MANUAL",
        "ABORTED_SAFETY",
        "AUDIO_DEVICE_ERROR",
        "GMS_UNAVAILABLE",
        "UNKNOWN",
    },
}


def is_valid_stt(text, no_speech_prob, prompt_text=""):
    """STT 출력이 유효한 발화인지 보수적으로 판정한다."""
    if not text or not text.strip():
        return False, "빈 출력"
    if no_speech_prob > 0.7:
        return False, "무음 확률 높음"
    tokens = text.split()
    if tokens and max(tokens.count(token) for token in set(tokens)) >= 4:
        return False, "반복 환각"

    normalize = lambda value: value.replace(" ", "").replace(",", "")
    prompt_tokens = {
        normalize(token)
        for token in prompt_text.replace(",", " ").split()
        if token.strip()
    }
    if prompt_tokens:
        hits = sum(
            1 for token in prompt_tokens if token and token in normalize(text)
        )
        if hits >= 3 or (
            len(prompt_tokens) >= 2 and hits == len(prompt_tokens)
        ):
            return False, "프롬프트 복사"
    return True, "ok"


def report_defaults() -> dict[str, Any]:
    """개인 자동 귀속과 진단을 피하는 33-6 안전 기본값."""
    return {
        "responseScope": "GROUP",
        # null은 마이크 오류 등으로 관찰 자체를 완료하지 못한 경우다.
        "anyResponseDetected": None,
        "reportedResponsiveCount": None,
        "reportedCountStatus": "UNKNOWN",
        "countConfidence": None,
        "mobilityStatus": "UNKNOWN",
        "urgentConditionReported": "UNKNOWN",
        "operatorReviewRequired": True,
        "terminationReason": "UNKNOWN",
    }


def coerce_report(value: Any) -> dict[str, Any]:
    """외부 JSON을 허용 필드만 가진 33-6 보고값으로 보정한다."""
    source = value if isinstance(value, dict) else {}
    report = report_defaults()

    for key in REPORT_FIELDS:
        if key in source:
            report[key] = source[key]

    for key, allowed in ENUMS.items():
        if not isinstance(report[key], str) or report[key] not in allowed:
            report[key] = report_defaults()[key]

    if report["anyResponseDetected"] is not None and not isinstance(
        report["anyResponseDetected"], bool
    ):
        report["anyResponseDetected"] = None

    count = report["reportedResponsiveCount"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        report["reportedResponsiveCount"] = None
        report["reportedCountStatus"] = "UNKNOWN"
    elif report["reportedCountStatus"] == "UNKNOWN":
        report["reportedCountStatus"] = "SELF_REPORTED_GROUP_COUNT"

    confidence = report["countConfidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        report["countConfidence"] = None
    elif not 0.0 <= float(confidence) <= 1.0:
        report["countConfidence"] = None
    else:
        report["countConfidence"] = float(confidence)

    if not isinstance(report["operatorReviewRequired"], bool):
        report["operatorReviewRequired"] = True

    # 단일 BRIO 100 마이크의 발화를 특정 개인에게 자동 귀속하지 않는다.
    report["responseScope"] = "GROUP"
    return report


def coerce_extraction(value: Any) -> dict[str, Any]:
    """GMS·폴백이 책임지는 세 가지 발화 추출 필드만 검증한다."""
    report = coerce_report(value)
    return {key: report[key] for key in EXTRACTION_FIELDS}


# 이전 호출부가 새 계약으로 점진적으로 이동할 수 있도록 이름만 호환한다.
coerce_defaults = coerce_report


def strip_hallucinated(info, source_text):
    """새 계약에는 자유형 신체 부위·위험 배열이 없어 추가 제거가 필요 없다."""
    return info


RISK_RULE_VERSION = "voice-risk-v1.1"

# 등급 계산을 방해하지 않는 종료 사유. 나머지는 부가 정보로만 기록한다.
COMPLETE_TERMINATIONS = frozenset({"NORMAL", "UNKNOWN"})

# 관찰 자체가 실패한 종료 사유. 무응답 판정의 근거로 쓰지 않는다.
# 계약상 이때 anyResponseDetected는 null이지만, false가 새어 들어오면
# "시스템 실패 = 요구조자 무응답"이 되므로 이 가드를 죽은 코드로 보고 빼면 안 된다.
SYSTEM_FAILURE_TERMINATIONS = frozenset(
    {"AUDIO_DEVICE_ERROR", "GMS_UNAVAILABLE"}
)


def risk_assessment(info):
    """관제 우선 확인용 위험 신호와 적용 근거를 반환한다.

    종료 사유는 게이트가 아니라 부가 정보다. 관찰이 완료됐다면 수집한 값으로
    등급을 계산하고, 미완료 사실은 근거에 덧붙인다. 관찰 자체를 못 한 경우
    (`anyResponseDetected`가 null)에만 `UNKNOWN`으로 단락한다.

    규칙표와 v1.0에서 바뀐 이유는 docs/README.md 2-2, 11-2.
    """
    report = coerce_report(info)
    termination = report["terminationReason"]
    incomplete = termination not in COMPLETE_TERMINATIONS

    def verdict(level, reasons):
        if incomplete:
            reasons = [*reasons, f"세션 미완료: {termination}"]
        return {
            "riskLevel": level,
            "riskReasons": reasons,
            "ruleVersion": RISK_RULE_VERSION,
            "operatorReviewRequired": True,
        }

    if report["anyResponseDetected"] is None:
        # 관찰 자체를 못 했다. 마이크 오류나 세션 미시작이 여기에 해당한다.
        return verdict("UNKNOWN", ["응답 여부를 관찰하지 못함"])
    if not report["anyResponseDetected"]:
        if termination in SYSTEM_FAILURE_TERMINATIONS:
            # 장치·연결 실패를 요구조자 무응답으로 바꾸지 않는다(명세 33-3).
            return verdict(
                "UNKNOWN", ["시스템 실패로 무응답을 확정할 수 없음"]
            )
        level = "IMMEDIATE"
        reasons = ["정상 청취 후 음성 응답이 감지되지 않음"]
    elif report["urgentConditionReported"] == "YES":
        level = "IMMEDIATE"
        reasons = ["긴급 상태가 있다고 발화함"]
    elif report["mobilityStatus"] == "NO":
        level = "URGENT"
        reasons = ["자력 이동이 불가능하다고 발화함"]
    elif (
        report["mobilityStatus"] == "YES"
        and report["urgentConditionReported"] == "NO"
    ):
        level = "DELAYED"
        reasons = ["자력 이동이 가능하고 긴급 상태가 없다고 발화함"]
    else:
        level = "UNKNOWN"
        reasons = ["우선 확인 참고값을 계산할 정보가 부족함"]

    return verdict(level, reasons)


def triage_rule(info):
    """기존 호출부 호환용. 새 코드에서는 risk_assessment를 사용한다."""
    return risk_assessment(info)["riskLevel"]

#!/usr/bin/env python3
"""공통 메시지 계약을 검증한다 (S15P11A301-128).

명세 31-12가 이 검사를 요구한다.

    Python과 Java가 같은 JSON 계약을 사용하도록 common/schemas에 JSON Schema를
    두고 CI에서 예제 메시지를 검증한다.

검증하는 것은 셋이다.

1. 스키마 파일 자체가 유효한 JSON Schema인가
2. common/samples의 예제가 봉투(31-5)와 messageType별 본문 스키마를 통과하는가
3. messageType마다 예제가 하나 이상 있는가

3번이 있어야 스키마만 추가하고 예제를 빼먹는 일을 막는다. 예제가 없는 스키마는
검증된 적이 없는 스키마다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("jsonschema가 필요하다: pip install jsonschema", file=sys.stderr)
    raise SystemExit(1)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "common" / "schemas"
SAMPLE_DIR = REPO_ROOT / "common" / "samples"

# messageType과 data 본문 스키마의 대응. 새 messageType을 추가하면 여기에도
# 등록해야 한다. 등록하지 않은 messageType이 예제에 나오면 실패한다.
DATA_SCHEMA_BY_TYPE = {
    "ROBOT_PRESENCE": "presence.schema.json",
    "ROBOT_STATE": "state.schema.json",
    "ROBOT_TELEMETRY": "telemetry.schema.json",
    "ENCOUNTER_CONFIRMED": "encounter.schema.json",
    "INTERACTION_REPORT": "interaction-report.schema.json",
    "MISSION_COMMAND": "mission-command.schema.json",
    "COMMAND_ACK": "command-ack.schema.json",
}

# 아직 봉투만 있고 본문 스키마를 만들지 않은 것. 후속 티켓에서 채운다.
# 여기 있는 동안은 예제가 없어도 통과시킨다.
PENDING_TYPES = {
    "MANUAL_DRIVE_COMMAND": "31-13 2단계 (ESP32 연동 이후)",
}


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def main() -> int:
    failures: list[str] = []

    envelope_path = SCHEMA_DIR / "envelope.schema.json"
    if not envelope_path.is_file():
        print(f"봉투 스키마가 없다: {envelope_path}", file=sys.stderr)
        return 1

    # 1. 스키마 자체가 유효한가.
    schemas: dict[str, dict] = {}
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        try:
            schema = load(schema_path)
            Draft202012Validator.check_schema(schema)
            schemas[schema_path.name] = schema
        except Exception as error:  # noqa: BLE001 - 어떤 오류든 그대로 보고한다
            failures.append(f"{schema_path.name}: 스키마가 유효하지 않다 - {error}")

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        return 1

    # format 키워드는 jsonschema가 기본적으로 검증하지 않는다. FORMAT_CHECKER를
    # 넘겨야 date-time이나 uuid가 실제로 검사된다. 다만 date-time 검사는 선택
    # 의존성(rfc3339-validator)이 있어야 동작하고 없으면 조용히 넘어가므로,
    # 스키마에는 format과 pattern을 함께 둔다. pattern이 실제 방어선이다.
    # (검증기를 만들 때 이것이 빠져 있어 잘못된 sentAt이 통과했다.)
    format_checker = Draft202012Validator.FORMAT_CHECKER
    envelope_validator = Draft202012Validator(
        schemas["envelope.schema.json"], format_checker=format_checker
    )

    # 봉투가 아는 messageType과 대응 표가 어긋나면 나중에 조용히 검증이 빠진다.
    declared_types = set(
        schemas["envelope.schema.json"]["properties"]["messageType"]["enum"]
    )
    mapped_types = set(DATA_SCHEMA_BY_TYPE) | set(PENDING_TYPES)
    if declared_types != mapped_types:
        only_envelope = declared_types - mapped_types
        only_table = mapped_types - declared_types
        if only_envelope:
            failures.append(
                "봉투의 messageType이 대응 표에 없다: " + ", ".join(sorted(only_envelope))
            )
        if only_table:
            failures.append(
                "대응 표의 messageType이 봉투에 없다: " + ", ".join(sorted(only_table))
            )

    # 2. 예제 검증.
    seen_types: set[str] = set()
    sample_paths = sorted(SAMPLE_DIR.glob("*.json"))
    if not sample_paths:
        failures.append(f"예제 메시지가 없다: {SAMPLE_DIR}")

    for sample_path in sample_paths:
        name = sample_path.name
        try:
            message = load(sample_path)
        except json.JSONDecodeError as error:
            failures.append(f"{name}: JSON 문법 오류 - {error}")
            continue

        errors = sorted(envelope_validator.iter_errors(message), key=lambda e: e.path)
        for error in errors:
            location = "/".join(str(part) for part in error.absolute_path) or "(최상위)"
            failures.append(f"{name}: 봉투 위반 [{location}] {error.message}")
        if errors:
            continue

        message_type = message["messageType"]
        seen_types.add(message_type)

        schema_name = DATA_SCHEMA_BY_TYPE.get(message_type)
        if schema_name is None:
            reason = PENDING_TYPES.get(message_type)
            if reason:
                failures.append(
                    f"{name}: {message_type}은 본문 스키마가 아직 없다({reason}). "
                    "예제를 추가하려면 스키마를 먼저 만든다."
                )
            else:
                failures.append(f"{name}: 알 수 없는 messageType {message_type}")
            continue

        if schema_name not in schemas:
            failures.append(f"{name}: 본문 스키마 파일이 없다 - {schema_name}")
            continue

        data_validator = Draft202012Validator(
            schemas[schema_name], format_checker=format_checker
        )
        for error in sorted(data_validator.iter_errors(message["data"]), key=lambda e: e.path):
            location = "data/" + "/".join(str(part) for part in error.absolute_path)
            failures.append(f"{name}: 본문 위반 [{location}] {error.message}")

    # 3. 본문 스키마가 있는 messageType은 예제가 하나 이상 있어야 한다.
    for message_type in sorted(DATA_SCHEMA_BY_TYPE):
        if message_type not in seen_types:
            failures.append(
                f"{message_type}: 예제 메시지가 없다. "
                f"common/samples 에 하나 이상 둔다."
            )

    if failures:
        print(f"공통 메시지 계약 검증 실패 {len(failures)}건", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(
        f"공통 메시지 계약 검증 통과. "
        f"스키마 {len(schemas)}개, 예제 {len(sample_paths)}개, "
        f"messageType {len(seen_types)}종."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

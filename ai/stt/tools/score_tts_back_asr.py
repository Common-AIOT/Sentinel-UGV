"""TTS 후보를 ASR로 다시 읽힌 JSON을 안전 문구 기준으로 채점한다.

입력의 ``records`` 각 항목은 expected, transcript와 선택적인 criticalTokens를 가진다.
숫자·부정어 같은 critical token 하나라도 사라지면 CER와 무관하게 실패한다.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from pathlib import Path


def normalize(text: str) -> str:
    return "".join(re.findall(r"[0-9a-z가-힣]+", text.lower()))


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, 1):
        current = [left_index]
        for right_index, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def score_record(record: dict, max_cer: float) -> dict:
    expected = normalize(record["expected"])
    transcript = normalize(record.get("transcript", ""))
    cer = edit_distance(expected, transcript) / max(len(expected), 1)
    missing = [
        token
        for token in record.get("criticalTokens", [])
        if normalize(token) not in transcript
    ]
    result = dict(record)
    result.update(
        {
            "cer": round(cer, 4),
            "missingCriticalTokens": missing,
            "status": "PASS" if cer <= max_cer and not missing else "FAIL",
        }
    )
    return result


def score_records(records: Iterable[dict], max_cer: float) -> list[dict]:
    return [score_record(record, max_cer) for record in records]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-cer", type=float, default=0.25)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = score_records(payload["records"], args.max_cer)
    failures = sum(row["status"] == "FAIL" for row in rows)
    report = {
        "schemaVersion": 1,
        "maxCer": args.max_cer,
        "summary": {
            "total": len(rows),
            "passed": len(rows) - failures,
            "failed": failures,
        },
        "records": rows,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

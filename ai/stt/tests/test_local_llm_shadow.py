from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bench.local_llm_shadow import call_local_model, load_unique_cases


def test_load_unique_cases_combines_datasets(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    base = {
        "text": "test",
        "expected": {
            "reportedResponsiveCount": None,
            "mobilityStatus": "UNKNOWN",
            "urgentConditionReported": "UNKNOWN",
        },
    }
    first.write_text(json.dumps({"cases": [{"caseId": "a", **base}]}), encoding="utf-8")
    second.write_text(
        json.dumps({"cases": [{"caseId": "b", **base}]}), encoding="utf-8"
    )

    assert [case.case_id for case in load_unique_cases([first, second])] == ["a", "b"]


def test_load_unique_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.json"
    dataset.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "caseId": "same",
                        "text": "test",
                        "expected": {
                            "reportedResponsiveCount": None,
                            "mobilityStatus": "UNKNOWN",
                            "urgentConditionReported": "UNKNOWN",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate caseId"):
        load_unique_cases([dataset, dataset])


def test_call_local_model_uses_auth_and_strict_schema() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"reportedResponsiveCount":1,'
                                '"mobilityStatus":"YES",'
                                '"urgentConditionReported":"NO"}'
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        actual, response = call_local_model(
            client,
            base_url="http://127.0.0.1:18200",
            api_key="secret",
            model="Qwen/Qwen3.5-4B",
            text="I can walk.",
        )

    assert actual["reportedResponsiveCount"] == 1
    assert response.total_tokens == 12

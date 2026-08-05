from __future__ import annotations

from fastapi.testclient import TestClient

from gpu_llm_server.app import Settings, create_app


class FakeExtractor:
    model_id = "Qwen/Qwen3.5-4B"

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple[list[dict[str, str]], int]] = []

    def load(self) -> None:
        pass

    def extract(self, messages: list[dict[str, str]], max_new_tokens: int) -> str:
        self.calls.append((messages, max_new_tokens))
        return self.output


def make_client(output: str, *, max_input_chars: int = 12_000) -> TestClient:
    settings = Settings(
        api_key="test-secret",
        max_input_chars=max_input_chars,
        max_new_tokens=100,
    )
    return TestClient(create_app(settings, FakeExtractor(output)))


def payload(content: str = "두 명이고 모두 걸을 수 있습니다.") -> dict:
    return {
        "model": "Qwen/Qwen3.5-4B",
        "messages": [{"role": "user", "content": content}],
        "max_completion_tokens": 160,
        "response_format": {"type": "json_object"},
    }


def test_health_is_public_and_reports_ready() -> None:
    with make_client(
        '{"reportedResponsiveCount":2,"mobilityStatus":"YES",'
        '"urgentConditionReported":"UNKNOWN"}'
    ) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ready": True,
        "model": "Qwen/Qwen3.5-4B",
        "error": None,
    }


def test_chat_requires_bearer_token() -> None:
    with make_client("{}") as client:
        response = client.post("/v1/chat/completions", json=payload())

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_chat_returns_openai_compatible_strict_json() -> None:
    output = (
        '{"reportedResponsiveCount":2,"mobilityStatus":"YES",'
        '"urgentConditionReported":"UNKNOWN"}'
    )
    with make_client(output) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-secret"},
            json=payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "Qwen/Qwen3.5-4B"
    assert body["choices"][0]["message"]["content"] == output


def test_chat_fails_closed_when_model_breaks_schema() -> None:
    with make_client('{"reportedResponsiveCount":0}') as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-secret"},
            json=payload(),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SCHEMA_INVALID"


def test_chat_rejects_oversized_input_without_echoing_it() -> None:
    secret_prompt = "do-not-echo"
    with make_client("{}", max_input_chars=3) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-secret"},
            json=payload(secret_prompt),
        )

    assert response.status_code == 413
    assert secret_prompt not in response.text
    assert response.json()["detail"]["code"] == "INPUT_TOO_LARGE"


def test_chat_rejects_request_without_user_message() -> None:
    body = payload()
    body["messages"] = [{"role": "system", "content": "extract"}]
    with make_client("{}") as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-secret"},
            json=body,
        )

    assert response.status_code == 422

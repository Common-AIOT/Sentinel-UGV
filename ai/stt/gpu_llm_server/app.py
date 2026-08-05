"""Small authenticated OpenAI-compatible Qwen3.5 extraction service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

LOGGER = logging.getLogger(__name__)

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reportedResponsiveCount": {"type": ["integer", "null"], "minimum": 1},
        "mobilityStatus": {"type": "string", "enum": ["YES", "NO", "UNKNOWN"]},
        "urgentConditionReported": {
            "type": "string",
            "enum": ["YES", "NO", "UNKNOWN"],
        },
    },
    "required": [
        "reportedResponsiveCount",
        "mobilityStatus",
        "urgentConditionReported",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Settings:
    model_id: str = "Qwen/Qwen3.5-4B"
    api_key: str = ""
    host: str = "127.0.0.1"
    port: int = 18200
    max_input_chars: int = 12_000
    max_new_tokens: int = 160
    cuda_visible_devices: str = "3"

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            model_id=os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen3.5-4B").strip(),
            api_key=os.getenv("LOCAL_LLM_API_KEY", ""),
            host=os.getenv("LOCAL_LLM_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("LOCAL_LLM_PORT", "18200")),
            max_input_chars=int(os.getenv("LOCAL_LLM_MAX_INPUT_CHARS", "12000")),
            max_new_tokens=int(os.getenv("LOCAL_LLM_MAX_NEW_TOKENS", "160")),
            cuda_visible_devices=os.getenv("LOCAL_LLM_CUDA_VISIBLE_DEVICES", "3"),
        )
        if not settings.api_key:
            raise ValueError("LOCAL_LLM_API_KEY must be set")
        if not 1 <= settings.port <= 65535:
            raise ValueError("LOCAL_LLM_PORT is invalid")
        return settings


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    model: str
    messages: list[Message] = Field(min_length=1, max_length=8)
    max_completion_tokens: int = Field(default=160, ge=1, le=512)
    response_format: dict[str, Any] | None = None

    @field_validator("messages")
    @classmethod
    def require_user_message(cls, messages: list[Message]) -> list[Message]:
        if not any(message.role == "user" for message in messages):
            raise ValueError("a user message is required")
        return messages


class Extractor(Protocol):
    model_id: str

    def load(self) -> None: ...

    def extract(self, messages: list[dict[str, str]], max_new_tokens: int) -> str: ...


class TransformersQwenExtractor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_id = settings.model_id
        self.processor = None
        self.model = None
        self.prefix_allowed_tokens_fn = None

    def load(self) -> None:
        os.environ["CUDA_VISIBLE_DEVICES"] = self.settings.cuda_visible_devices
        import torch
        from transformers import (
            AutoModelForImageTextToText,
            AutoProcessor,
            PreTrainedTokenizer,
            tokenization_utils,
        )

        # lm-format-enforcer 0.11 still imports this Transformers 4.x alias.
        # Transformers 5 removed the alias, while retaining the tokenizer contract.
        if not hasattr(tokenization_utils, "PreTrainedTokenizerBase"):
            tokenization_utils.PreTrainedTokenizerBase = PreTrainedTokenizer

        from lmformatenforcer import JsonSchemaParser
        from lmformatenforcer.integrations.transformers import (
            build_token_enforcer_tokenizer_data,
            build_transformers_prefix_allowed_tokens_fn,
        )

        self.processor = AutoProcessor.from_pretrained(self.model_id)
        self.model = AutoModelForImageTextToText.from_pretrained(
            self.model_id,
            dtype=torch.bfloat16,
            device_map="cuda:0",
        )
        parser = JsonSchemaParser(EXTRACTION_SCHEMA)
        tokenizer_data = build_token_enforcer_tokenizer_data(self.processor.tokenizer)
        self.prefix_allowed_tokens_fn = build_transformers_prefix_allowed_tokens_fn(
            tokenizer_data, parser
        )

    def extract(self, messages: list[dict[str, str]], max_new_tokens: int) -> str:
        if self.processor is None or self.model is None:
            raise RuntimeError("model is not loaded")
        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            chat_template_kwargs={"enable_thinking": False},
        )
        inputs = {key: value.to(self.model.device) for key, value in inputs.items()}
        generated = self.model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            prefix_allowed_tokens_fn=self.prefix_allowed_tokens_fn,
        )
        prompt_length = inputs["input_ids"].shape[-1]
        return self.processor.decode(
            generated[0][prompt_length:], skip_special_tokens=True
        ).strip()


def _validate_extraction(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != set(EXTRACTION_SCHEMA["required"]):
        raise ValueError("output does not match extraction schema")
    count = value["reportedResponsiveCount"]
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count < 1
    ):
        raise ValueError("reportedResponsiveCount is invalid")
    for field in ("mobilityStatus", "urgentConditionReported"):
        if value[field] not in {"YES", "NO", "UNKNOWN"}:
            raise ValueError(f"{field} is invalid")
    return value


def create_app(
    settings: Settings | None = None, extractor: Extractor | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()
    model = extractor or TransformersQwenExtractor(settings)
    state = {"ready": extractor is not None, "error": None}
    semaphore = asyncio.Semaphore(1)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if extractor is None:
            try:
                await asyncio.to_thread(model.load)
                state["ready"] = True
            except Exception as error:
                state["error"] = type(error).__name__
                LOGGER.exception("local LLM model load failed")
        yield

    app = FastAPI(title="A301 Local LLM", version="1.0", lifespan=lifespan)

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization[7:]
        if not secrets.compare_digest(supplied, settings.api_key):
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED"})

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "retryable": False}},
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok" if state["ready"] else "degraded",
            "ready": state["ready"],
            "model": model.model_id,
            "error": state["error"],
        }

    @app.post("/v1/chat/completions", dependencies=[Depends(authenticate)])
    async def chat(payload: ChatRequest) -> dict[str, Any]:
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_READY"})
        prompt_chars = sum(len(message.content) for message in payload.messages)
        if prompt_chars > settings.max_input_chars:
            raise HTTPException(status_code=413, detail={"code": "INPUT_TOO_LARGE"})
        started = time.perf_counter()
        async with semaphore:
            raw = await asyncio.to_thread(
                model.extract,
                [message.model_dump() for message in payload.messages],
                min(payload.max_completion_tokens, settings.max_new_tokens),
            )
        try:
            value = _validate_extraction(raw)
        except (json.JSONDecodeError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail={"code": "SCHEMA_INVALID"}
            ) from error
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "model": model.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
            "server_metrics": {
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)
            },
        }

    return app

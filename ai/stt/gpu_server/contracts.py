"""Versioned HTTP response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    ready: bool
    backend: str
    model: str
    cuda_visible_devices: str
    error_code: str | None = None


class TranscriptionResponse(BaseModel):
    api_version: Literal["v1"] = "v1"
    request_id: str
    text: str
    language: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_seconds: float = Field(ge=0.0)
    inference_ms: float = Field(ge=0.0)
    backend: str
    model: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorBody

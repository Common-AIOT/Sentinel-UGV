"""FastAPI application factory for the versioned ASR API."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .audio_input import choose_suffix, inspect_audio
from .backends import ASRBackend, create_backend
from .config import SUPPORTED_LANGUAGE_CODES, Settings
from .contracts import ErrorBody, ErrorResponse, HealthResponse, TranscriptionResponse
from .errors import BackendInferenceError, ServiceError

LOGGER = logging.getLogger("sentinel.gpu_asr")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _request_id(request: Request) -> str:
    value = request.headers.get("x-request-id", "")
    if REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _error_response(request: Request, error: ServiceError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid.uuid4().hex)
    payload = ErrorResponse(
        error=ErrorBody(
            code=error.code,
            message=error.message,
            request_id=request_id,
            retryable=error.retryable,
        )
    )
    return JSONResponse(status_code=error.status_code, content=payload.model_dump())


def create_app(
    settings: Settings | None = None,
    backend: ASRBackend | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    backend = backend or create_backend(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.ready = False
        application.state.error_code = None
        application.state.gate = asyncio.Semaphore(settings.max_concurrency)
        if not settings.auth_configured:
            application.state.error_code = "AUTH_NOT_CONFIGURED"
        else:
            try:
                await run_in_threadpool(backend.load)
                application.state.ready = True
            except Exception:
                application.state.error_code = "MODEL_LOAD_FAILED"
                LOGGER.exception(
                    "asr_model_load_failed backend=%s model=%s",
                    backend.name,
                    backend.model_id,
                )
        try:
            yield
        finally:
            application.state.ready = False
            await run_in_threadpool(backend.close)

    app = FastAPI(
        title="Sentinel GPU ASR",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.backend = backend

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = _request_id(request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, error: ServiceError):
        return _error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _error: RequestValidationError):
        return _error_response(
            request,
            ServiceError(422, "INVALID_REQUEST", "The request fields are invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _error: Exception):
        LOGGER.exception(
            "asr_request_failed request_id=%s",
            getattr(request.state, "request_id", "unknown"),
        )
        return _error_response(
            request,
            ServiceError(
                500,
                "INTERNAL_ERROR",
                "The ASR service failed to process the request.",
                retryable=True,
            ),
        )

    async def authorize(request: Request) -> None:
        if settings.allow_unauthenticated:
            return
        if not settings.api_key:
            raise ServiceError(
                503,
                "AUTH_NOT_CONFIGURED",
                "The ASR service is not configured for authenticated requests.",
                retryable=True,
            )
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
        candidate = bearer or request.headers.get("x-api-key", "")
        if not candidate or not hmac.compare_digest(candidate, settings.api_key):
            raise ServiceError(401, "UNAUTHORIZED", "Valid ASR credentials are required.")

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        ready = bool(getattr(request.app.state, "ready", False))
        return HealthResponse(
            status="ok" if ready else "degraded",
            ready=ready,
            backend=backend.name,
            model=backend.model_id,
            cuda_visible_devices=settings.cuda_visible_devices,
            error_code=getattr(request.app.state, "error_code", None),
        )

    @app.post(
        "/v1/asr",
        response_model=TranscriptionResponse,
        dependencies=[Depends(authorize)],
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            415: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def transcribe(
        request: Request,
        audio: Annotated[UploadFile, File()],
        language: Annotated[str | None, Form()] = None,
    ) -> TranscriptionResponse:
        if not request.app.state.ready:
            raise ServiceError(
                503,
                "MODEL_NOT_READY",
                "The ASR model is not ready.",
                retryable=True,
            )
        normalized_language = language.strip().lower() if language else None
        if normalized_language and normalized_language not in SUPPORTED_LANGUAGE_CODES:
            raise ServiceError(
                422,
                "UNSUPPORTED_LANGUAGE",
                "The requested language is not supported.",
            )

        payload = await audio.read(settings.max_audio_bytes + 1)
        if len(payload) > settings.max_audio_bytes:
            raise ServiceError(
                413,
                "AUDIO_TOO_LARGE",
                f"Audio payload exceeds {settings.max_audio_bytes} bytes.",
            )
        if not payload:
            raise ServiceError(400, "EMPTY_AUDIO", "The uploaded audio is empty.")
        suffix = choose_suffix(audio.filename, audio.content_type)

        path: Path | None = None
        acquired = False
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=suffix, prefix="sentinel-asr-", delete=False
            ) as temporary:
                temporary.write(payload)
                path = Path(temporary.name)
            metadata = await run_in_threadpool(inspect_audio, path, settings)
            try:
                await asyncio.wait_for(
                    request.app.state.gate.acquire(),
                    timeout=settings.queue_timeout_seconds,
                )
                acquired = True
            except TimeoutError as exc:
                raise ServiceError(
                    429,
                    "ASR_OVERLOADED",
                    "The ASR service is at capacity.",
                    retryable=True,
                ) from exc

            started = time.perf_counter()
            try:
                result = await run_in_threadpool(
                    backend.transcribe, path, normalized_language
                )
            except BackendInferenceError as exc:
                raise ServiceError(
                    503,
                    "MODEL_INFERENCE_FAILED",
                    "The ASR model could not complete inference.",
                    retryable=True,
                ) from exc
            inference_ms = (time.perf_counter() - started) * 1000.0
            return TranscriptionResponse(
                request_id=request.state.request_id,
                text=result.text,
                language=result.language,
                confidence=result.confidence,
                duration_seconds=metadata.duration_seconds,
                inference_ms=inference_ms,
                backend=backend.name,
                model=backend.model_id,
            )
        finally:
            if acquired:
                request.app.state.gate.release()
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    return app

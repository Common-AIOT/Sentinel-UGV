"""GPU ASR FastAPI를 호출하는 동기 Jetson 클라이언트.

오디오 원문이나 API 키를 로그 메시지·예외 문자열에 넣지 않는다. 서버 장애는
``RemoteASRError``의 안정된 코드로만 노출해 대화 계층이 요구조자 무응답과
시스템 실패를 구분할 수 있게 한다.
"""

from __future__ import annotations

import io
import time
import uuid
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
import numpy as np
import soundfile as sf


class RemoteASRError(RuntimeError):
    """원격 ASR 호출 실패. 메시지에는 민감한 응답 본문을 포함하지 않는다."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class RemoteASRClient:
    """인증·타임아웃·제한 재시도를 적용한 원격 ASR 어댑터."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 8.0,
        connect_timeout_seconds: float = 2.0,
        max_attempts: int = 2,
        retry_delay_seconds: float = 0.2,
        allow_insecure_http: bool = False,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self.sleep = sleep

        self._validate(allow_insecure_http)
        timeout = httpx.Timeout(
            timeout_seconds,
            connect=connect_timeout_seconds,
        )
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def _validate(self, allow_insecure_http: bool) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("SENTINEL_ASR_BASE_URL must be an absolute HTTP URL")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not loopback and not allow_insecure_http:
            raise ValueError("remote ASR requires HTTPS outside loopback")
        if not self.api_key:
            raise ValueError("SENTINEL_ASR_API_KEY is required for remote ASR")
        if self.max_attempts < 1:
            raise ValueError("SENTINEL_ASR_MAX_ATTEMPTS must be at least 1")
        if self.retry_delay_seconds < 0:
            raise ValueError("SENTINEL_ASR_RETRY_DELAY must not be negative")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> dict[str, object]:
        """서버가 실제 추론 가능한 상태인지 확인한다.

        단순히 URL 형식과 API 키 존재만 검사하면 Jetson 배포 직전에 방화벽,
        터널, 모델 로드 실패를 놓친다. health 응답에는 키나 오디오가 없으며,
        예외에도 서버 응답 본문을 넣지 않는다.
        """
        request_id = f"jetson-health-{uuid.uuid4().hex}"
        last_error: RemoteASRError | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.get(
                    f"{self.base_url}/health",
                    headers={"X-Request-ID": request_id},
                )
                try:
                    body = response.json()
                except ValueError as error:
                    raise RemoteASRError(
                        "ASR_INVALID_RESPONSE",
                        retryable=True,
                        status_code=response.status_code,
                    ) from error

                if response.is_error:
                    raise RemoteASRError(
                        f"ASR_HTTP_{response.status_code}",
                        retryable=response.status_code in {429, 502, 503, 504},
                        status_code=response.status_code,
                    )
                if not isinstance(body, dict) or not isinstance(
                    body.get("ready"), bool
                ):
                    raise RemoteASRError(
                        "ASR_INVALID_RESPONSE",
                        retryable=True,
                        status_code=response.status_code,
                    )
                if not body["ready"]:
                    raise RemoteASRError(
                        str(body.get("error_code") or "ASR_NOT_READY"),
                        retryable=True,
                        status_code=response.status_code,
                    )
                return body
            except httpx.TimeoutException as error:
                last_error = RemoteASRError("ASR_TIMEOUT", retryable=True)
                last_error.__cause__ = error
            except httpx.TransportError as error:
                last_error = RemoteASRError("ASR_UNAVAILABLE", retryable=True)
                last_error.__cause__ = error
            except RemoteASRError as error:
                last_error = error

            if not last_error.retryable or attempt >= self.max_attempts:
                raise last_error
            if self.retry_delay_seconds:
                self.sleep(self.retry_delay_seconds * attempt)

        raise last_error or RemoteASRError("ASR_UNAVAILABLE", retryable=True)

    def transcribe(
        self,
        wav: np.ndarray,
        *,
        sample_rate: int,
        language: str = "ko",
    ) -> tuple[str, float]:
        payload = self._wav_payload(wav, sample_rate)
        request_id = f"jetson-{uuid.uuid4().hex}"

        last_error: RemoteASRError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/asr",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-Request-ID": request_id,
                    },
                    data={"language": language},
                    files={"audio": ("sentinel.wav", payload, "audio/wav")},
                )
                result = self._parse_response(response)
                text = result.strip()
                # Qwen3-ASR은 Whisper의 no_speech_prob을 제공하지 않는다. VAD를 이미
                # 통과한 발화이므로 비어 있지 않은 전사는 0, 빈 전사는 1로만 매핑한다.
                return text, 0.0 if text else 1.0
            except httpx.TimeoutException as error:
                last_error = RemoteASRError("ASR_TIMEOUT", retryable=True)
                last_error.__cause__ = error
            except httpx.TransportError as error:
                last_error = RemoteASRError("ASR_UNAVAILABLE", retryable=True)
                last_error.__cause__ = error
            except RemoteASRError as error:
                last_error = error

            if not last_error.retryable or attempt >= self.max_attempts:
                raise last_error
            if self.retry_delay_seconds:
                self.sleep(self.retry_delay_seconds * attempt)

        raise last_error or RemoteASRError("ASR_UNAVAILABLE", retryable=True)

    @staticmethod
    def _wav_payload(wav: np.ndarray, sample_rate: int) -> bytes:
        array = np.asarray(wav, dtype=np.float32).reshape(-1)
        if array.size == 0 or sample_rate <= 0:
            raise ValueError("audio must not be empty")
        if not bool(np.isfinite(array).all()):
            raise ValueError("audio contains non-finite samples")
        buffer = io.BytesIO()
        sf.write(
            buffer,
            np.clip(array, -1.0, 1.0),
            sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        return buffer.getvalue()

    @staticmethod
    def _parse_response(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError as error:
            raise RemoteASRError(
                "ASR_INVALID_RESPONSE", retryable=True, status_code=response.status_code
            ) from error

        if response.is_error:
            error_body = body.get("error") if isinstance(body, dict) else None
            code = (
                error_body.get("code")
                if isinstance(error_body, dict)
                else None
            )
            retryable = (
                bool(error_body.get("retryable"))
                if isinstance(error_body, dict)
                else response.status_code in {429, 502, 503, 504}
            )
            raise RemoteASRError(
                str(code or f"ASR_HTTP_{response.status_code}"),
                retryable=retryable,
                status_code=response.status_code,
            )

        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise RemoteASRError(
                "ASR_INVALID_RESPONSE", retryable=True, status_code=response.status_code
            )
        return text

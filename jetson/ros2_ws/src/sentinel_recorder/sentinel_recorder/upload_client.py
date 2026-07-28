"""Presigned URL 기반 미디어 업로드 (S15P11A301-124, 명세 31-7).

명세가 정한 흐름을 그대로 따른다.

    1. 젯슨이 이벤트 MP4를 로컬에 안전하게 마무리한다        (S15P11A301-123)
    2. POST /api/v1/media/uploads 로 파일명·크기·체크섬·encounterId 전송
    3. Spring Boot가 짧은 유효기간의 Presigned PUT URL 반환
    4. 젯슨이 Spring Boot를 거치지 않고 스토리지에 직접 업로드
    5. 완료 API 호출 → storage_status = AVAILABLE
    6. 실패하면 UPLOAD_PENDING으로 두고 재시도

## Presigned URL을 캐시하지 않는다

URL은 유효기간이 짧다. 재시도할 때 옛 URL을 쓰면 만료돼 실패하고, 그 실패를
네트워크 문제로 오해한다. 31-10이 "복구 후 Presigned URL **재발급** 후 업로드"라고
한 이유다. 매 시도마다 2단계부터 다시 한다.

## object key를 서버가 정한다

31-11: "Presigned URL은 짧은 시간만 유효하게 발급하고 `object_key`와 파일 종류를
**서버가 결정한다**." 젯슨이 키를 정하면 임의 경로에 쓸 수 있다. 그래서 힌트만
보내고 응답의 `objectKey`를 권위로 삼는다.

## ROS도 파일 스캔도 모른다

HTTP만 다루므로 브로커나 ROS 없이 단독 시험할 수 있다. 어떤 이벤트를 언제 올릴지는
`upload_worker`가 정한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# 명세 31-7의 엔드포인트. 백엔드 구현과 어긋나는 부분은
# common/schemas/media-*.schema.json 에 적었다.
UPLOAD_URL_PATH = '/api/v1/media/uploads'
COMPLETE_PATH_TEMPLATE = '/api/v1/media/uploads/{media_id}/complete'

KIND_VIDEO = 'EVENT_VIDEO'
KIND_THUMBNAIL = 'THUMBNAIL'

# 업로드는 사람을 기다리게 하지 않으므로 넉넉히 준다. 다만 무한정 기다리면 워커가
# 다음 이벤트를 못 올리므로 상한을 둔다. 2.5Mbps 5분 영상이 약 94MB이고 Wi-Fi에서
# 몇 십 초면 끝난다.
REQUEST_TIMEOUT_SECONDS = 15
PUT_TIMEOUT_SECONDS = 180


class UploadError(RuntimeError):
    """업로드 실패. `retryable`이 False면 재시도해도 같은 결과다."""

    def __init__(self, reason: str, detail: str = '', retryable: bool = True) -> None:
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail
        self.retryable = retryable


@dataclass
class UploadTarget:
    """올릴 파일 하나."""

    path: Path
    kind: str
    sha256: str
    size_bytes: int
    content_type: str


@dataclass
class UploadOutcome:
    object_key: str
    size_bytes: int
    sha256: str
    already_complete: bool = False


class UploadClient:
    """백엔드와 스토리지에 대한 HTTP 호출만 담당한다."""

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        request_timeout: int = REQUEST_TIMEOUT_SECONDS,
        put_timeout: int = PUT_TIMEOUT_SECONDS,
        auth_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.session = session or requests.Session()
        self.request_timeout = request_timeout
        self.put_timeout = put_timeout
        self._headers: dict[str, str] = {}
        if auth_token:
            # 36장이 인터넷 구간 조종 API에 짧은 배포 환경 토큰을 요구한다.
            # 미디어 업로드도 같은 경로를 쓰면 여기로 들어온다.
            self._headers['Authorization'] = f'Bearer {auth_token}'

    # ------------------------------------------------------------------
    # 2~3단계. Presigned URL 발급
    # ------------------------------------------------------------------

    def request_upload_url(
        self,
        *,
        encounter_id: str,
        media_id: str,
        target: UploadTarget,
        suggested_key: str | None = None,
    ) -> tuple[str, str]:
        """(presigned_url, object_key)를 돌려준다.

        `object_key`는 **응답의 값**이다. 우리가 보낸 힌트가 아니다(31-11).
        """
        body: dict[str, Any] = {
            'encounterId': encounter_id,
            'mediaId': media_id,
            'kind': target.kind,
            'fileName': target.path.name,
            'sizeBytes': target.size_bytes,
            'sha256': target.sha256,
            'contentType': target.content_type,
            'suggestedKey': suggested_key,
        }
        url = f'{self.base_url}{UPLOAD_URL_PATH}'
        try:
            response = self.session.post(
                url, json=body, headers=self._headers, timeout=self.request_timeout
            )
        except requests.RequestException as error:
            raise UploadError('PRESIGN_UNREACHABLE', str(error)[:200])

        if response.status_code >= 500:
            raise UploadError(
                'PRESIGN_SERVER_ERROR', f'{response.status_code} {response.text[:150]}'
            )
        if response.status_code >= 400:
            # 4xx는 우리 요청이 잘못된 것이므로 재시도해도 같다. 계약 불일치를
            # 네트워크 문제로 오해하지 않도록 구분한다.
            raise UploadError(
                'PRESIGN_REJECTED',
                f'{response.status_code} {response.text[:150]}',
                retryable=False,
            )

        presigned, object_key = self._parse_presign(response)
        if not presigned:
            raise UploadError(
                'PRESIGN_MALFORMED',
                f'응답에 URL이 없다: {response.text[:150]}',
                retryable=False,
            )
        return presigned, object_key or (suggested_key or target.path.name)

    @staticmethod
    def _parse_presign(response: requests.Response) -> tuple[str | None, str | None]:
        """응답에서 URL과 object key를 꺼낸다.

        백엔드가 `ApiResponse<PresignedUrlResponse>`로 감싸므로 `data` 안에 있을 수
        있고, 감싸지 않을 수도 있다. 둘 다 받아준다. 감싸는 방식이 바뀌었을 때
        업로드가 조용히 멈추는 것보다 낫다.
        """
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None, None
        if not isinstance(payload, dict):
            return None, None
        body = payload.get('data') if isinstance(payload.get('data'), dict) else payload
        url = body.get('url') or body.get('presignedUrl') or body.get('uploadUrl')
        key = body.get('objectKey') or body.get('key')
        return (str(url) if url else None), (str(key) if key else None)

    # ------------------------------------------------------------------
    # 4단계. 스토리지 직접 업로드
    # ------------------------------------------------------------------

    def put_object(self, presigned_url: str, target: UploadTarget) -> None:
        """Presigned URL로 파일을 올린다. 백엔드를 거치지 않는다.

        스트리밍으로 보낸다. 5분 영상이 약 94MB이므로 전부 메모리에 올리면
        젯슨 8GB에서 다른 노드를 압박한다.

        `Content-Type`은 발급 요청과 같아야 한다. S3 서명이 헤더를 포함하므로
        다르면 403이 난다.
        """
        headers = {'Content-Type': target.content_type}
        try:
            with target.path.open('rb') as handle:
                response = self.session.put(
                    presigned_url,
                    data=handle,
                    headers=headers,
                    timeout=self.put_timeout,
                )
        except OSError as error:
            raise UploadError('LOCAL_READ_FAILED', str(error)[:200], retryable=False)
        except requests.RequestException as error:
            raise UploadError('PUT_FAILED', str(error)[:200])

        if response.status_code in (401, 403):
            # 서명 만료가 가장 흔하다. 재시도할 때는 URL을 다시 발급받아야 한다.
            raise UploadError(
                'PUT_FORBIDDEN',
                f'{response.status_code} 서명 만료나 헤더 불일치로 보인다',
            )
        if response.status_code >= 400:
            raise UploadError('PUT_FAILED', f'{response.status_code} {response.text[:150]}')

    # ------------------------------------------------------------------
    # 5단계. 완료 등록
    # ------------------------------------------------------------------

    def complete(
        self,
        *,
        media_id: str,
        encounter_id: str,
        object_key: str,
        target: UploadTarget,
        duration_seconds: float | None = None,
        recorded: dict[str, Any] | None = None,
    ) -> bool:
        """완료를 알린다. 이미 완료된 것도 성공으로 본다.

        멱등해야 한다. 응답을 못 받아 재시도하면 같은 `mediaId`로 두 번 오는데,
        그때 오류로 처리하면 영원히 `UPLOAD_PENDING`에 갇힌다. 그래서 409를
        성공으로 취급한다.

        **404는 엔드포인트 미구현을 뜻할 수 있다.** 백엔드에 `/complete`가 아직
        없으므로(27.4·31-7이 요구하는데도) 그 경우를 구분해 알린다. 재시도해도
        같으므로 retryable=False다.
        """
        body: dict[str, Any] = {
            'encounterId': encounter_id,
            'objectKey': object_key,
            'sizeBytes': target.size_bytes,
            'sha256': target.sha256,
            'kind': target.kind,
            'durationSeconds': duration_seconds,
            'recorded': recorded,
        }
        url = f'{self.base_url}{COMPLETE_PATH_TEMPLATE.format(media_id=media_id)}'
        try:
            response = self.session.post(
                url, json=body, headers=self._headers, timeout=self.request_timeout
            )
        except requests.RequestException as error:
            raise UploadError('COMPLETE_UNREACHABLE', str(error)[:200])

        if response.status_code == 409:
            return True
        if response.status_code == 404:
            raise UploadError(
                'COMPLETE_NOT_IMPLEMENTED',
                '백엔드에 /uploads/{mediaId}/complete 가 없다. '
                '명세 27.4와 31-7이 요구하는 엔드포인트다.',
                retryable=False,
            )
        if response.status_code >= 500:
            raise UploadError(
                'COMPLETE_SERVER_ERROR', f'{response.status_code} {response.text[:150]}'
            )
        if response.status_code >= 400:
            raise UploadError(
                'COMPLETE_REJECTED',
                f'{response.status_code} {response.text[:150]}',
                retryable=False,
            )
        return False

    # ------------------------------------------------------------------
    # 2~5단계 묶음
    # ------------------------------------------------------------------

    def upload(
        self,
        *,
        encounter_id: str,
        media_id: str,
        target: UploadTarget,
        suggested_key: str | None = None,
        duration_seconds: float | None = None,
        recorded: dict[str, Any] | None = None,
        skip_complete: bool = False,
    ) -> UploadOutcome:
        """발급 → 업로드 → 완료를 한 번에 한다.

        `skip_complete`는 백엔드에 `/complete`가 없는 동안 업로드 경로만 검증하기
        위한 것이다. 운영에서는 쓰지 않는다. 켜면 서버가 `AVAILABLE`을 모르므로
        다시보기 목록에 나타나지 않는다.
        """
        presigned, object_key = self.request_upload_url(
            encounter_id=encounter_id,
            media_id=media_id,
            target=target,
            suggested_key=suggested_key,
        )
        self.put_object(presigned, target)

        if skip_complete:
            return UploadOutcome(
                object_key=object_key,
                size_bytes=target.size_bytes,
                sha256=target.sha256,
            )

        already = self.complete(
            media_id=media_id,
            encounter_id=encounter_id,
            object_key=object_key,
            target=target,
            duration_seconds=duration_seconds,
            recorded=recorded,
        )
        return UploadOutcome(
            object_key=object_key,
            size_bytes=target.size_bytes,
            sha256=target.sha256,
            already_complete=already,
        )

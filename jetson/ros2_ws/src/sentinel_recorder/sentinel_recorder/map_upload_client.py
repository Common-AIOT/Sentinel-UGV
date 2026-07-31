"""지도 업로드 HTTP 계약 (S15P11A301-171, 백엔드는 S15P11A301-185).

    POST /api/v1/maps/uploads                    { missionId }
      -> { mapId, pgmKey, yamlKey, pgmUrl, yamlUrl, contentType, expiresInSec }
    PUT  <pgmUrl>, <yamlUrl>                     presigned, 스토리지 직접
    POST /api/v1/maps/uploads/{mapId}/complete   -> { mapId }

미디어 업로드(31-7)와 절차는 같지만 계약이 다르다. 발급 한 번에 **두 객체**의
URL이 함께 오고, 요청 본문은 `missionId` 하나다.

`UploadClient`를 상속하지 않고 **가지고 있는다**. `put_object`는 presigned URL과
파일만 쓰는 범용 동작이라 그대로 재사용할 수 있지만, 상속하면 미디어용
`request_upload_url`·`complete`가 이 클래스의 표면에 섞여 어느 계약을 부르는지
헷갈린다. 이벤트 영상 업로드는 동작 중인 데모 경로이므로 건드리지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .map_upload import PGM_CONTENT_TYPE, YAML_CONTENT_TYPE
from .upload_client import (
    PUT_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    UploadClient,
    UploadError,
    UploadTarget,
)

MAP_UPLOAD_PATH = '/api/v1/maps/uploads'
MAP_COMPLETE_PATH_TEMPLATE = '/api/v1/maps/uploads/{map_id}/complete'


@dataclass
class MapPresign:
    """발급 응답. 두 객체가 한 번에 온다."""

    map_id: str
    pgm_url: str
    yaml_url: str
    pgm_key: str
    yaml_key: str
    content_type: str = ''


class MapUploadClient:
    """지도 발급·PUT·완료. 상태를 갖지 않는다."""

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
        # put_object를 빌려 쓰기 위한 위임 대상. 세션을 공유해 연결을
        # 재사용한다.
        self._media = UploadClient(
            base_url,
            session=session,
            request_timeout=request_timeout,
            put_timeout=put_timeout,
            auth_token=auth_token,
        )
        self.session = self._media.session
        self.request_timeout = request_timeout
        self._headers: dict[str, str] = {}
        if auth_token:
            self._headers['Authorization'] = f'Bearer {auth_token}'

    # ------------------------------------------------------------------
    # 발급
    # ------------------------------------------------------------------

    def request_upload(
        self, *, mission_id: str, map_id: str | None = None
    ) -> MapPresign:
        """URL 두 개와 mapId를 받는다.

        `map_id`를 주면 백엔드가 그 값으로 maps.id를 만든다(선택 필드,
        S15P11A301-193). 젯슨이 임무 시작에 정한 값을 임무 내내 쓰기 위한
        것이며, 같은 임무에 이미 지도가 있으면 기존 행이 그대로 온다.

        **그래도 쓰는 것은 응답의 mapId다.** 우리가 보낸 값과 다를 수 있다 —
        백엔드가 그 필드를 모르는 버전이면 서버 생성 값이 온다. 그때 우리
        값을 쓰면 관제와 어긋나므로, 호출자가 차이를 보고 판단하게 한다.
        """
        url = f'{self.base_url}{MAP_UPLOAD_PATH}'
        payload: dict[str, Any] = {'missionId': mission_id}
        if map_id:
            payload['mapId'] = map_id
        try:
            response = self.session.post(
                url,
                json=payload,
                headers=self._headers,
                timeout=self.request_timeout,
            )
        except requests.RequestException as error:
            raise UploadError('PRESIGN_UNREACHABLE', str(error)[:200])

        if response.status_code >= 500:
            raise UploadError(
                'PRESIGN_SERVER_ERROR', f'{response.status_code} {response.text[:150]}'
            )
        if response.status_code >= 400:
            # 4xx는 우리 요청이 잘못된 것이므로 재시도해도 같다. missionId가
            # 임무 테이블에 없는 경우가 여기로 온다.
            raise UploadError(
                'PRESIGN_REJECTED',
                f'{response.status_code} {response.text[:150]}',
                retryable=False,
            )

        presign = self._parse(response)
        if presign is None:
            raise UploadError(
                'PRESIGN_MALFORMED',
                f'응답에 mapId나 URL이 없다: {response.text[:150]}',
                retryable=False,
            )
        return presign

    @staticmethod
    def _parse(response: requests.Response) -> MapPresign | None:
        """`ApiResponse<MapUploadResponse>`와 벗은 형태를 모두 받는다.

        감싸는 방식이 바뀌었을 때 업로드가 조용히 멈추는 것보다 낫다.
        upload_client._parse_presign과 같은 이유의 관용이다.
        """
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        body: Any = payload.get('data')
        if not isinstance(body, dict):
            body = payload

        map_id = body.get('mapId')
        pgm_url = body.get('pgmUrl')
        yaml_url = body.get('yamlUrl')
        if not (map_id and pgm_url and yaml_url):
            return None
        return MapPresign(
            map_id=str(map_id),
            pgm_url=str(pgm_url),
            yaml_url=str(yaml_url),
            pgm_key=str(body.get('pgmKey') or ''),
            yaml_key=str(body.get('yamlKey') or ''),
            content_type=str(body.get('contentType') or ''),
        )

    # ------------------------------------------------------------------
    # PUT
    # ------------------------------------------------------------------

    def put_pair(self, presign: MapPresign, *, pgm: Path, yaml_path: Path) -> None:
        """pgm과 yaml을 올린다. 하나라도 실패하면 예외.

        Content-Type은 발급 응답의 값을 우선한다. S3 서명이 헤더를 포함하므로
        발급 때와 다르면 403이 난다. 응답이 비어 있으면 파일 종류로 정한다.
        """
        pgm_type = presign.content_type or PGM_CONTENT_TYPE
        yaml_type = presign.content_type or YAML_CONTENT_TYPE
        for path, target_url, content_type in (
            (pgm, presign.pgm_url, pgm_type),
            (yaml_path, presign.yaml_url, yaml_type),
        ):
            self._media.put_object(
                target_url,
                UploadTarget(
                    path=path,
                    kind='MAP',
                    sha256='',
                    size_bytes=path.stat().st_size if path.exists() else 0,
                    content_type=content_type,
                ),
            )

    # ------------------------------------------------------------------
    # 완료
    # ------------------------------------------------------------------

    def complete(
        self, *, map_id: str, body: dict[str, Any] | None = None
    ) -> bool:
        """등록을 알린다. 이미 등록된 것도 성공으로 본다.

        `body`에 지도 메타데이터를 실어 보낸다(S15P11A301-193). 전부 선택
        필드이므로 비어 있으면 본문 없이 부른다 — 백엔드가 그때는 yaml에서
        읽는 기존 동작을 유지한다.

        409를 성공으로 보는 이유는 MQTT QoS 1과 같은 문제다 - PUT은 성공했는데
        완료 응답을 못 받으면 다시 시도하게 되고, 그때 409가 온다. 이것을
        실패로 보면 영구히 재시도한다.
        """
        url = f'{self.base_url}{MAP_COMPLETE_PATH_TEMPLATE.format(map_id=map_id)}'
        try:
            response = self.session.post(
                url,
                json=body or None,
                headers=self._headers,
                timeout=self.request_timeout,
            )
        except requests.RequestException as error:
            raise UploadError('COMPLETE_UNREACHABLE', str(error)[:200])

        if response.status_code == 409:
            return True
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

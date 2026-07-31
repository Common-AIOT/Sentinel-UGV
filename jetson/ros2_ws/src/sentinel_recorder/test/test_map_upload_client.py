"""지도 업로드 HTTP 계약 테스트 (S15P11A301-171 / 백엔드 S15P11A301-185).

실제 요청을 보내지 않는다. 가짜 세션으로 응답만 흉내내 **응답 형태가 바뀌었을
때 업로드가 조용히 멈추는** 경우를 잡는다.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

requests = pytest.importorskip('requests')

from sentinel_recorder.map_upload_client import (  # noqa: E402
    MAP_UPLOAD_PATH,
    MapUploadClient,
)
from sentinel_recorder.upload_client import UploadError  # noqa: E402

MISSION = '11111111-2222-3333-4444-555555555555'
MAP_ID = '99999999-8888-7777-6666-555555555555'


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else '')

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError('없음', '', 0)
        return self._payload


class FakeSession:
    """post/put을 기록하고 미리 정한 응답을 돌려준다."""

    def __init__(self, post=None, put=None):
        self._post = post or FakeResponse(200, {})
        self._put = put or FakeResponse(200)
        self.posts: list[tuple[str, dict]] = []
        self.puts: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json or {}))
        return self._post(url) if callable(self._post) else self._post

    def put(self, url, data=None, headers=None, timeout=None):
        self.puts.append((url, headers or {}))
        return self._put


def _presign_payload(wrapped: bool):
    body = {
        'mapId': MAP_ID,
        'pgmKey': f'maps/{MAP_ID}/map.pgm',
        'yamlKey': f'maps/{MAP_ID}/map.yaml',
        'pgmUrl': 'https://storage/pgm?sig=1',
        'yamlUrl': 'https://storage/yaml?sig=2',
        'contentType': 'application/octet-stream',
        'expiresInSec': 600,
    }
    return {'data': body} if wrapped else body


# ----------------------------------------------------------------------
# 발급
# ----------------------------------------------------------------------


@pytest.mark.parametrize('wrapped', [True, False])
def test_감싼_응답과_벗은_응답을_모두_읽는다(wrapped):
    """ApiResponse<T>로 감싸는 방식이 바뀌어도 멈추지 않아야 한다."""
    session = FakeSession(post=FakeResponse(200, _presign_payload(wrapped)))
    client = MapUploadClient('https://api', session=session)
    presign = client.request_upload(mission_id=MISSION)
    assert presign.map_id == MAP_ID
    assert presign.pgm_url == 'https://storage/pgm?sig=1'
    assert presign.yaml_key.endswith('map.yaml')


def test_요청_본문은_missionId_하나다():
    """계약은 missionId뿐이다(MapUploadRequest)."""
    session = FakeSession(post=FakeResponse(200, _presign_payload(True)))
    MapUploadClient('https://api', session=session).request_upload(mission_id=MISSION)
    url, body = session.posts[0]
    assert url == f'https://api{MAP_UPLOAD_PATH}'
    assert body == {'missionId': MISSION}


def test_mapId가_없으면_형식_오류로_본다():
    """URL만 오고 mapId가 없으면 등록을 완료할 수 없다."""
    payload = _presign_payload(False)
    del payload['mapId']
    session = FakeSession(post=FakeResponse(200, payload))
    client = MapUploadClient('https://api', session=session)
    with pytest.raises(UploadError) as caught:
        client.request_upload(mission_id=MISSION)
    assert caught.value.reason == 'PRESIGN_MALFORMED'
    assert caught.value.retryable is False


def test_4xx는_재시도하지_않는다():
    """임무가 없는 missionId 등 우리 요청이 틀린 경우다."""
    session = FakeSession(post=FakeResponse(400, None, 'bad request'))
    client = MapUploadClient('https://api', session=session)
    with pytest.raises(UploadError) as caught:
        client.request_upload(mission_id=MISSION)
    assert caught.value.reason == 'PRESIGN_REJECTED'
    assert caught.value.retryable is False


def test_5xx는_재시도한다():
    session = FakeSession(post=FakeResponse(503, None, 'unavailable'))
    client = MapUploadClient('https://api', session=session)
    with pytest.raises(UploadError) as caught:
        client.request_upload(mission_id=MISSION)
    assert caught.value.reason == 'PRESIGN_SERVER_ERROR'
    assert caught.value.retryable is True


def test_연결_실패는_재시도한다():
    class Dead(FakeSession):
        def post(self, *a, **k):
            raise requests.RequestException('연결 없음')

    client = MapUploadClient('https://api', session=Dead())
    with pytest.raises(UploadError) as caught:
        client.request_upload(mission_id=MISSION)
    assert caught.value.reason == 'PRESIGN_UNREACHABLE'
    assert caught.value.retryable is True


# ----------------------------------------------------------------------
# PUT
# ----------------------------------------------------------------------


def test_두_파일을_각_URL로_올린다(tmp_path):
    pgm = tmp_path / 'map.pgm'
    yaml_path = tmp_path / 'map.yaml'
    pgm.write_bytes(b'P5 data')
    yaml_path.write_bytes(b'resolution: 0.05')

    session = FakeSession(post=FakeResponse(200, _presign_payload(True)))
    client = MapUploadClient('https://api', session=session)
    presign = client.request_upload(mission_id=MISSION)
    client.put_pair(presign, pgm=pgm, yaml_path=yaml_path)

    assert [u for u, _ in session.puts] == [
        'https://storage/pgm?sig=1',
        'https://storage/yaml?sig=2',
    ]


def test_Content_Type은_발급_응답을_따른다(tmp_path):
    """S3 서명이 헤더를 포함하므로 발급 때와 다르면 403이 난다."""
    pgm = tmp_path / 'map.pgm'
    yaml_path = tmp_path / 'map.yaml'
    pgm.write_bytes(b'x')
    yaml_path.write_bytes(b'y')

    session = FakeSession(post=FakeResponse(200, _presign_payload(True)))
    client = MapUploadClient('https://api', session=session)
    presign = client.request_upload(mission_id=MISSION)
    client.put_pair(presign, pgm=pgm, yaml_path=yaml_path)

    for _, headers in session.puts:
        assert headers['Content-Type'] == 'application/octet-stream'


def test_응답에_Content_Type이_없으면_파일_종류로_정한다(tmp_path):
    pgm = tmp_path / 'map.pgm'
    yaml_path = tmp_path / 'map.yaml'
    pgm.write_bytes(b'x')
    yaml_path.write_bytes(b'y')

    payload = _presign_payload(False)
    payload['contentType'] = ''
    session = FakeSession(post=FakeResponse(200, payload))
    client = MapUploadClient('https://api', session=session)
    presign = client.request_upload(mission_id=MISSION)
    client.put_pair(presign, pgm=pgm, yaml_path=yaml_path)

    types = [h['Content-Type'] for _, h in session.puts]
    assert types == ['image/x-portable-graymap', 'application/x-yaml']


# ----------------------------------------------------------------------
# 완료
# ----------------------------------------------------------------------


def test_완료_경로에_mapId가_들어간다():
    session = FakeSession(post=FakeResponse(200, {'data': {'mapId': MAP_ID}}))
    client = MapUploadClient('https://api', session=session)
    assert client.complete(map_id=MAP_ID) is False
    url, _ = session.posts[0]
    assert url == f'https://api/api/v1/maps/uploads/{MAP_ID}/complete'


def test_409는_성공으로_본다():
    """PUT은 됐는데 완료 응답을 못 받아 재시도한 경우다.

    실패로 보면 영구히 재시도한다. MQTT QoS 1의 중복 전달과 같은 문제다.
    """
    session = FakeSession(post=FakeResponse(409, None, 'already'))
    client = MapUploadClient('https://api', session=session)
    assert client.complete(map_id=MAP_ID) is True


def test_완료_5xx는_재시도한다():
    session = FakeSession(post=FakeResponse(500, None, 'oops'))
    client = MapUploadClient('https://api', session=session)
    with pytest.raises(UploadError) as caught:
        client.complete(map_id=MAP_ID)
    assert caught.value.retryable is True


def test_base_url의_끝_슬래시를_흡수한다():
    session = FakeSession(post=FakeResponse(200, _presign_payload(True)))
    client = MapUploadClient('https://api/', session=session)
    client.request_upload(mission_id=MISSION)
    assert session.posts[0][0] == f'https://api{MAP_UPLOAD_PATH}'

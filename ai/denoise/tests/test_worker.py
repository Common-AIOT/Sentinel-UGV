"""worker의 흐름·멱등성·실패 구분 검증.

백엔드 없이 돈다. `requests.Session`을 흉내내는 가짜 세션으로 7단계 호출을
기록하고, 응답 코드를 바꿔 실패 경로를 만든다. DeepFilterNet이 필요한 경로는
`enhance_media`를 대체해 분리했다 — 워커가 검증하는 것은 오케스트레이션이고
잡음 제거 자체는 test_enhance_media.py가 본다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker as worker_module  # noqa: E402
from worker import (  # noqa: E402
    KIND_DENOISED_AUDIO,
    MediaApi,
    WorkerError,
    denoised_media_id,
    find_media,
    process_encounter,
    sha256_of,
)

VIDEO_ID = "33333333-3333-4333-8333-333333333333"
ENCOUNTER_ID = "22222222-2222-4222-8222-222222222222"


# ── 가짜 HTTP ─────────────────────────────────────────────────────
class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None, text: str = "", body: bytes = b""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self._body = body

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload

    def iter_content(self, _size):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeSession:
    """경로별 응답을 미리 정해두고 호출을 기록한다."""

    def __init__(self, *, detail_media=None, overrides=None, video_bytes=b"FAKEMP4"):
        self.calls: list[tuple[str, str]] = []
        self.overrides = overrides or {}
        self.video_bytes = video_bytes
        self.detail_media = (
            detail_media
            if detail_media is not None
            else [{"mediaId": VIDEO_ID, "type": "EVENT_VIDEO", "storageStatus": "AVAILABLE"}]
        )

    def _maybe_override(self, key):
        return self.overrides.get(key)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        if "/encounters/" in url:
            forced = self._maybe_override("encounter")
            return forced or FakeResponse(
                payload={"data": {"id": ENCOUNTER_ID, "media": self.detail_media}}
            )
        if "/view-url" in url:
            forced = self._maybe_override("view_url")
            return forced or FakeResponse(
                payload={"data": {"objectKey": "k", "url": "https://s3/get", "expiresInSec": 600}}
            )
        if url.startswith("https://s3/get"):
            forced = self._maybe_override("download")
            return forced or FakeResponse(body=self.video_bytes)
        raise AssertionError(f"예상하지 못한 GET {url}")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        if url.endswith("/media/uploads"):
            forced = self._maybe_override("presign")
            return forced or FakeResponse(
                payload={
                    "data": {
                        "objectKey": "missions/m/encounters/e/event-denoised.m4a",
                        "url": "https://s3/put",
                        "contentType": "audio/mp4",
                    }
                }
            )
        if url.endswith("/complete"):
            forced = self._maybe_override("complete")
            return forced or FakeResponse(payload={"data": {"storageStatus": "AVAILABLE"}})
        raise AssertionError(f"예상하지 못한 POST {url}")

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url))
        forced = self._maybe_override("put")
        return forced or FakeResponse()


@pytest.fixture
def fake_enhance(monkeypatch):
    """enhance_media를 대체한다. 모델 없이 워커 흐름만 본다."""
    from enhance_media import EnhanceResult

    def stub(source, output=None, **_kwargs):
        target = Path(output) if output else Path(source).with_suffix(".m4a")
        target.write_bytes(b"DENOISED-AUDIO-BYTES")
        return EnhanceResult(target, 30.6, 1.2)

    monkeypatch.setattr(worker_module, "enhance_media", stub)
    return stub


# ── 순수 함수 ─────────────────────────────────────────────────────
def test_media_id_is_deterministic_and_distinct():
    a = denoised_media_id(VIDEO_ID)
    b = denoised_media_id(VIDEO_ID)
    assert a == b, "재시도해도 같아야 서버가 중복 등록을 막는다"
    assert a != VIDEO_ID
    assert a != denoised_media_id("44444444-4444-4444-8444-444444444444")


def test_media_id_differs_from_thumbnail_derivation():
    """썸네일과 네임스페이스가 달라야 같은 영상의 두 자산이 충돌하지 않는다."""
    import uuid

    thumbnail_ns = uuid.UUID("6f9c1a52-3f4e-4b8a-9d21-7c5e0b8f4a13")
    thumbnail = str(uuid.uuid5(thumbnail_ns, f"{VIDEO_ID}:thumbnail"))
    assert denoised_media_id(VIDEO_ID) != thumbnail


def test_find_media_ignores_pending():
    media = [
        {"mediaId": "x", "type": "EVENT_VIDEO", "storageStatus": "UPLOAD_PENDING"},
        {"mediaId": "y", "type": "EVENT_VIDEO", "storageStatus": "AVAILABLE"},
    ]
    assert find_media({"media": media}, "EVENT_VIDEO")["mediaId"] == "y"
    assert find_media({"media": []}, "EVENT_VIDEO") is None


def test_sha256_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "a.bin"
    path.write_bytes(b"hello world")
    digest, size = sha256_of(path)
    assert digest == hashlib.sha256(b"hello world").hexdigest()
    assert size == 11


# ── 정상 흐름 ─────────────────────────────────────────────────────
def test_happy_path_calls_seven_steps_in_order(fake_enhance):
    session = FakeSession()
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "UPLOADED"
    assert outcome.media_id == denoised_media_id(VIDEO_ID)
    assert outcome.seconds == pytest.approx(30.6)

    steps = [f"{m} {u}" for m, u in session.calls]
    assert "/api/v1/encounters/" in steps[0]
    assert "/view-url" in steps[1]
    assert steps[2].startswith("GET https://s3/get")
    assert steps[3].endswith("/api/v1/media/uploads")
    assert steps[4] == "PUT https://s3/put"
    assert steps[5].endswith("/complete")
    assert len(steps) == 6  # 다운로드가 GET 하나라 7단계가 6호출이다


def test_dry_run_does_not_upload(fake_enhance):
    session = FakeSession()
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID, dry_run=True)

    assert outcome.status == "DRY_RUN"
    assert not any(method == "PUT" for method, _ in session.calls)
    assert not any(url.endswith("/media/uploads") for _, url in session.calls)


def test_already_available_skips_everything(fake_enhance):
    session = FakeSession(
        detail_media=[
            {"mediaId": VIDEO_ID, "type": "EVENT_VIDEO", "storageStatus": "AVAILABLE"},
            {"mediaId": "any", "type": KIND_DENOISED_AUDIO, "storageStatus": "AVAILABLE"},
        ]
    )
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "ALREADY_DONE"
    assert len(session.calls) == 1  # encounter 조회 한 번뿐


def test_missing_video_is_not_a_failure(fake_enhance):
    session = FakeSession(detail_media=[])
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "NO_VIDEO"


def test_video_without_audio_track_is_not_a_failure(monkeypatch):
    """마이크가 없으면 젯슨이 비디오만 기록한다. 정상 경로다."""
    from enhance_media import NoAudioTrack

    def raising(*_args, **_kwargs):
        raise NoAudioTrack("오디오 트랙 없음")

    monkeypatch.setattr(worker_module, "enhance_media", raising)
    session = FakeSession()
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "NO_AUDIO"
    assert not any(method == "PUT" for method, _ in session.calls)


def test_complete_409_counts_as_success(fake_enhance):
    session = FakeSession(overrides={"complete": FakeResponse(status_code=409, payload={})})
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "ALREADY_DONE"


# ── 실패 구분 ─────────────────────────────────────────────────────
def test_missing_kind_is_flagged_as_precondition(fake_enhance):
    """백엔드가 kind를 모르면 재시도해도 같다. 선행 조건으로 구분한다."""
    session = FakeSession(
        overrides={
            "presign": FakeResponse(
                status_code=400,
                payload={"message": f"kind: {KIND_DENOISED_AUDIO}"},
                text=f'{{"message":"지원하지 않는 kind: {KIND_DENOISED_AUDIO}"}}',
            )
        }
    )
    api = MediaApi("http://backend:8080", session=session)

    with pytest.raises(WorkerError) as caught:
        process_encounter(api, ENCOUNTER_ID)

    assert caught.value.reason == "KIND_NOT_SUPPORTED"
    assert caught.value.retryable is False
    assert "@Pattern" in caught.value.detail  # 무엇을 고쳐야 하는지 알려준다


def test_server_error_is_retryable(fake_enhance):
    session = FakeSession(
        overrides={"presign": FakeResponse(status_code=503, text="upstream down")}
    )
    api = MediaApi("http://backend:8080", session=session)

    with pytest.raises(WorkerError) as caught:
        process_encounter(api, ENCOUNTER_ID)

    assert caught.value.retryable is True


def test_expired_signature_is_retryable(fake_enhance):
    session = FakeSession(overrides={"put": FakeResponse(status_code=403, text="expired")})
    api = MediaApi("http://backend:8080", session=session)

    with pytest.raises(WorkerError) as caught:
        process_encounter(api, ENCOUNTER_ID)

    assert caught.value.reason == "PUT_FORBIDDEN"
    assert caught.value.retryable is True


def test_empty_download_is_retryable(fake_enhance):
    session = FakeSession(video_bytes=b"")
    api = MediaApi("http://backend:8080", session=session)

    with pytest.raises(WorkerError) as caught:
        process_encounter(api, ENCOUNTER_ID)

    assert caught.value.reason == "DOWNLOAD_EMPTY"
    assert caught.value.retryable is True


def test_unwrapped_envelope_is_accepted(fake_enhance):
    """봉투 없이 오는 응답도 받아준다. 감싸는 방식이 바뀌어도 멈추지 않는다."""
    session = FakeSession(
        overrides={
            "view_url": FakeResponse(payload={"url": "https://s3/get", "objectKey": "k"})
        }
    )
    api = MediaApi("http://backend:8080", session=session)

    outcome = process_encounter(api, ENCOUNTER_ID)

    assert outcome.status == "UPLOADED"

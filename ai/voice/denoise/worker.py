"""encounter 하나의 이벤트 영상에서 잡음 제거본을 만들어 관제에 등록한다.

    python worker.py --encounter <encounterId>
    python worker.py --encounter <id> --api https://api.sentinel-ugv.xyz
    python worker.py --encounter <id> --atten-lim-db 12
    python worker.py --encounter <id> --dry-run       올리지 않고 만들기만

## 흐름

    1. GET  /api/v1/encounters/{encounterId}          media 목록에서 EVENT_VIDEO 찾기
    2. GET  /api/v1/media/{videoMediaId}/view-url     다운로드용 Presigned GET
    3. 그 URL로 MP4 내려받기 (임시 파일)
    4. enhance_media()                               추출 → 잡음 제거 → m4a
    5. POST /api/v1/media/uploads                    업로드용 Presigned PUT
    6. PUT  m4a
    7. POST /api/v1/media/uploads/{mediaId}/complete → AVAILABLE

## S3 자격증명을 갖지 않는다

다운로드는 `view-url`, 업로드는 `uploads`가 발급하는 Presigned URL로 한다. 둘 다
백엔드가 서명하므로 이 워커는 스토리지 키를 알 필요가 없다. 젯슨 업로더가 같은
방식이다(`jetson/.../upload_client.py`). 키를 늘리지 않는 편이 안전하고 배포도 쉽다.

## 한 건 처리하고 죽는다

상주하지 않는다. 처리 자체는 1~2분 영상에서 1~3초인데 상주하면 torch가 평소
700MB를 점유한다. 뜨고 죽으면 평소 점유가 0이고 작업당 약 7초다(import·모델 로딩
포함). 아무도 기다리지 않는 후처리라 이 편이 낫다.
근거: `docs/08-AI-음성.md` 33.9

## 멱등하다

`mediaId`를 영상 `mediaId`에서 uuid5로 파생시킨다. 재시도해도 같은 값이므로 서버가
중복 등록을 막는다(31-10). 썸네일이 쓰는 방식과 같다. 이미 `AVAILABLE`이면
아무것도 하지 않고 성공으로 끝낸다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enhance_media import (  # noqa: E402
    ATTEN_LIMIT_DB,
    NoAudioTrack,
    SilentAudioTrack,
    enhance_media,
)

# 백엔드가 이 kind를 받아야 5단계가 통과한다. 아직 없으면 400이 나며, 그때는
# 재시도해도 같으므로 선행 조건 미충족으로 구분해 알린다.
KIND_DENOISED_AUDIO = "EVENT_AUDIO_DENOISED"
KIND_EVENT_VIDEO = "EVENT_VIDEO"
CONTENT_TYPE = "audio/mp4"
FILE_NAME = "event-denoised.m4a"

# 썸네일(`upload_worker.THUMBNAIL_NAMESPACE`)과 다른 네임스페이스를 쓴다. 같은 영상에서
# 파생되는 두 자산이 절대 같은 UUID를 갖지 않게 한다.
DENOISED_NAMESPACE = uuid.UUID("9e2b7d14-5c83-4f6a-a0d7-1b4e8c93f205")

REQUEST_TIMEOUT_SECONDS = 15
TRANSFER_TIMEOUT_SECONDS = 180


class WorkerError(RuntimeError):
    """작업 실패. `retryable`이 False면 재시도해도 같은 결과다."""

    def __init__(self, reason: str, detail: str = "", retryable: bool = True) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.retryable = retryable


@dataclass(frozen=True)
class Outcome:
    """처리 결과. `status`로 무엇이 일어났는지 구분한다."""

    # UPLOADED · ALREADY_DONE · NO_AUDIO · SILENT_AUDIO · NO_VIDEO · DRY_RUN
    #
    # NO_AUDIO와 SILENT_AUDIO는 갈라 둔다. 앞은 정상 경로(오디오 없는 영상)이고
    # 뒤는 마이크 사망이다. 둘 다 업로드하지 않지만 뒤는 경보를 남긴다.
    status: str
    detail: str = ""
    media_id: str | None = None
    seconds: float | None = None


def denoised_media_id(video_media_id: str) -> str:
    """영상 mediaId에서 제거본 mediaId를 파생한다. 같은 입력이면 같은 출력이다."""
    return str(uuid.uuid5(DENOISED_NAMESPACE, f"{video_media_id}:denoised-audio"))


def sha256_of(path: Path) -> tuple[str, int]:
    """(소문자 16진수 해시, 바이트 수). 완료 API가 크기를 검증한다."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


class MediaApi:
    """백엔드 REST 호출만 담당한다. ROS·스토리지를 모른다."""

    def __init__(
        self,
        base_url: str,
        *,
        session: Any = None,
        auth_token: str | None = None,
    ) -> None:
        import requests

        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.headers: dict[str, str] = {}
        if auth_token:
            self.headers["Authorization"] = f"Bearer {auth_token}"

    # ── 응답 해석 ─────────────────────────────────────────────────
    @staticmethod
    def _unwrap(response: Any, what: str) -> dict[str, Any]:
        """`ApiResponse{data,message,status}` 봉투를 벗긴다.

        봉투가 없는 응답도 받아준다. 감싸는 방식이 바뀌었을 때 조용히 멈추는 것보다
        낫다(젯슨 `upload_client._parse_presign`과 같은 판단).
        """
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise WorkerError(
                f"{what}_MALFORMED", f"JSON 아님: {response.text[:150]}", retryable=False
            )
        if not isinstance(payload, dict):
            raise WorkerError(f"{what}_MALFORMED", "객체가 아님", retryable=False)
        body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return body

    def _check(self, response: Any, what: str) -> None:
        if response.status_code >= 500:
            raise WorkerError(
                f"{what}_SERVER_ERROR", f"{response.status_code} {response.text[:150]}"
            )
        if response.status_code >= 400:
            # 4xx는 우리 요청이 잘못된 것이므로 재시도해도 같다. 계약 불일치를
            # 네트워크 문제로 오해하지 않도록 구분한다.
            raise WorkerError(
                f"{what}_REJECTED",
                f"{response.status_code} {response.text[:200]}",
                retryable=False,
            )

    def _get(self, path: str, what: str) -> dict[str, Any]:
        import requests

        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                headers=self.headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise WorkerError(f"{what}_UNREACHABLE", str(error)[:200])
        self._check(response, what)
        return self._unwrap(response, what)

    # ── 1단계 ─────────────────────────────────────────────────────
    def encounter_detail(self, encounter_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/encounters/{encounter_id}", "ENCOUNTER")

    # ── 2단계 ─────────────────────────────────────────────────────
    def view_url(self, media_id: str) -> str:
        body = self._get(f"/api/v1/media/{media_id}/view-url", "VIEW_URL")
        url = body.get("url") or body.get("presignedUrl")
        if not url:
            raise WorkerError("VIEW_URL_MALFORMED", "응답에 url이 없다", retryable=False)
        return str(url)

    # ── 3단계 ─────────────────────────────────────────────────────
    def download(self, url: str, target: Path) -> None:
        import requests

        try:
            with self.session.get(
                url, stream=True, timeout=TRANSFER_TIMEOUT_SECONDS
            ) as response:
                self._check(response, "DOWNLOAD")
                with target.open("wb") as handle:
                    for block in response.iter_content(1 << 20):
                        if block:
                            handle.write(block)
        except requests.RequestException as error:
            raise WorkerError("DOWNLOAD_FAILED", str(error)[:200])
        if target.stat().st_size == 0:
            raise WorkerError("DOWNLOAD_EMPTY", "0바이트를 받았다")

    # ── 5단계 ─────────────────────────────────────────────────────
    def request_upload(
        self, *, encounter_id: str, media_id: str, sha256: str, size_bytes: int
    ) -> tuple[str, str]:
        """(presigned_url, object_key). `object_key`는 **응답의 값**이다(31-11)."""
        import requests

        body = {
            "encounterId": encounter_id,
            "mediaId": media_id,
            "kind": KIND_DENOISED_AUDIO,
            "fileName": FILE_NAME,
            "sizeBytes": size_bytes,
            "sha256": sha256,
            "contentType": CONTENT_TYPE,
        }
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/media/uploads",
                json=body,
                headers=self.headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise WorkerError("PRESIGN_UNREACHABLE", str(error)[:200])

        if response.status_code == 400 and KIND_DENOISED_AUDIO in response.text:
            # 백엔드가 이 kind를 아직 모른다. 선행 조건이므로 재시도 대상이 아니다.
            raise WorkerError(
                "KIND_NOT_SUPPORTED",
                f"백엔드가 {KIND_DENOISED_AUDIO}를 받지 않는다. "
                "UploadUrlRequest·MediaCompleteRequest의 @Pattern과 "
                "MediaService.objectKey()·contentType() 확장이 선행이다.",
                retryable=False,
            )
        self._check(response, "PRESIGN")

        parsed = self._unwrap(response, "PRESIGN")
        url = parsed.get("url") or parsed.get("presignedUrl") or parsed.get("uploadUrl")
        key = parsed.get("objectKey") or parsed.get("key")
        if not url:
            raise WorkerError("PRESIGN_MALFORMED", "응답에 URL이 없다", retryable=False)
        return str(url), str(key or FILE_NAME)

    # ── 6단계 ─────────────────────────────────────────────────────
    def put_object(self, url: str, path: Path) -> None:
        """Presigned URL로 올린다. `Content-Type`은 발급 요청과 같아야 한다(다르면 403)."""
        import requests

        try:
            with path.open("rb") as handle:
                response = self.session.put(
                    url,
                    data=handle,
                    headers={"Content-Type": CONTENT_TYPE},
                    timeout=TRANSFER_TIMEOUT_SECONDS,
                )
        except requests.RequestException as error:
            raise WorkerError("PUT_FAILED", str(error)[:200])
        if response.status_code in (401, 403):
            # 서명 만료가 가장 흔하다. 재시도 시 URL을 다시 발급받아야 한다.
            raise WorkerError(
                "PUT_FORBIDDEN", f"{response.status_code} 서명 만료나 헤더 불일치"
            )
        self._check(response, "PUT")

    # ── 7단계 ─────────────────────────────────────────────────────
    def complete(
        self,
        *,
        media_id: str,
        encounter_id: str,
        object_key: str,
        sha256: str,
        size_bytes: int,
        duration_seconds: float | None,
    ) -> bool:
        """완료를 알린다. 이미 완료된 것(409)도 성공으로 본다."""
        import requests

        body = {
            "encounterId": encounter_id,
            "objectKey": object_key,
            "sizeBytes": size_bytes,
            "sha256": sha256,
            "kind": KIND_DENOISED_AUDIO,
            "durationSeconds": duration_seconds,
        }
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/media/uploads/{media_id}/complete",
                json=body,
                headers=self.headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise WorkerError("COMPLETE_UNREACHABLE", str(error)[:200])
        if response.status_code == 409:
            return True
        self._check(response, "COMPLETE")
        return False


def find_media(detail: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """`AVAILABLE`인 자산만 돌려준다. 업로드 중인 것을 입력으로 쓰면 안 된다."""
    for item in detail.get("media") or []:
        if item.get("type") == kind and item.get("storageStatus") == "AVAILABLE":
            return item
    return None


def process_encounter(
    api: MediaApi,
    encounter_id: str,
    *,
    atten_lim_db: float | None = ATTEN_LIMIT_DB,
    dry_run: bool = False,
    workdir: Path | None = None,
) -> Outcome:
    """encounter 하나를 처리한다. 이미 되어 있으면 아무것도 하지 않는다."""
    detail = api.encounter_detail(encounter_id)

    video = find_media(detail, KIND_EVENT_VIDEO)
    if video is None:
        # 영상이 아직 안 올라왔거나 없는 encounter다. 실패가 아니다.
        return Outcome("NO_VIDEO", "AVAILABLE 상태의 EVENT_VIDEO가 없다")

    video_media_id = str(video["mediaId"])
    media_id = denoised_media_id(video_media_id)

    if find_media(detail, KIND_DENOISED_AUDIO) is not None:
        return Outcome("ALREADY_DONE", "제거본이 이미 AVAILABLE", media_id)

    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        source = Path(tmp) / "event.mp4"
        api.download(api.view_url(video_media_id), source)

        try:
            result = enhance_media(
                source,
                Path(tmp) / FILE_NAME,
                atten_lim_db=atten_lim_db,
                quiet=True,
            )
        except NoAudioTrack as error:
            # 마이크가 없거나 열리지 않으면 젯슨이 비디오만 기록한다
            # (sentinel_streaming이 오디오를 끄고 재구성). 정상 경로다.
            return Outcome("NO_AUDIO", str(error)[:120])
        except SilentAudioTrack as error:
            # 트랙은 있는데 내용이 0이다. 정상 경로가 아니라 캡처 경로 사망이다.
            #
            # 업로드하지 않는다. 무음을 올리면 스캔이 그 발견을 완료로 표시해
            # 마이크 사망이 영구히 덮인다. 올리지 않으면 스캔이 "rc 0인데 자산
            # 없음"으로 보고 skip 처리하므로(deploy/denoise_scan.py) 무한 재스캔도
            # 나지 않는다 — NO_AUDIO와 같은 취급이다.
            #
            # 대신 소리를 낸다. 이 줄이 scan.log에 남는 것이 유일한 경보다.
            print(
                f"[ALERT] 마이크 점검 필요 — {error}\n"
                "        젯슨에서 기본 입력 소스가 실제 마이크인지 확인한다:\n"
                "          pactl info | grep -i 'default source'\n"
                "        (S15P11A301-257)",
                file=sys.stderr,
            )
            return Outcome("SILENT_AUDIO", str(error)[:120])

        sha256, size_bytes = sha256_of(result.path)
        if dry_run:
            return Outcome(
                "DRY_RUN",
                f"{result.path.name} {size_bytes}B (업로드 생략)",
                media_id,
                result.seconds,
            )

        presigned, object_key = api.request_upload(
            encounter_id=encounter_id,
            media_id=media_id,
            sha256=sha256,
            size_bytes=size_bytes,
        )
        api.put_object(presigned, result.path)
        already = api.complete(
            media_id=media_id,
            encounter_id=encounter_id,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
            duration_seconds=result.seconds,
        )

    return Outcome(
        "ALREADY_DONE" if already else "UPLOADED",
        f"{object_key} · {size_bytes}B · {result.seconds:.1f}초",
        media_id,
        result.seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--encounter", required=True, help="처리할 encounterId (UUID)")
    parser.add_argument(
        "--api",
        default="http://localhost:8080",
        help="백엔드 주소. 같은 도커 네트워크면 http://backend:8080",
    )
    parser.add_argument("--token", default=None, help="Authorization Bearer 토큰")
    parser.add_argument(
        "--atten-lim-db",
        type=float,
        default=ATTEN_LIMIT_DB,
        help="최대 감쇠 상한(dB). 생략하면 무제한",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="제거본만 만들고 업로드하지 않는다"
    )
    args = parser.parse_args()

    api = MediaApi(args.api, auth_token=args.token)
    try:
        outcome = process_encounter(
            api,
            args.encounter,
            atten_lim_db=args.atten_lim_db,
            dry_run=args.dry_run,
        )
    except WorkerError as error:
        # 재시도 가능 여부를 종료 코드로 구분한다. 호출자(백엔드·cron)가 이 값으로
        # 재시도할지 포기할지 정할 수 있다.
        kind = "재시도 가능" if error.retryable else "재시도 무의미"
        print(f"실패 ({kind}): {error}", file=sys.stderr)
        return 1 if error.retryable else 2

    print(f"{outcome.status}: {outcome.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

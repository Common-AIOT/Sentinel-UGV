"""지도 업로드 판단 로직 (S15P11A301-171).

rclpy도 requests도 import하지 않는다. "무엇을 올릴지, 언제 다시 시도할지,
보고서를 어떻게 갱신할지"만 담아 CI에서 검사한다. HTTP는
`map_upload_client.py`, ROS는 `map_uploader_node.py`가 맡는다.

`report.json`이 저장(S15P11A301-171 전반부)과 업로드 사이의 유일한 인계
지점이다. 프로세스가 죽어도 파일에 남은 상태로 이어받는다 — 31-10이 요구하는
"망이 없어도 잃지 않는다"가 메모리 상태로는 성립하지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# map_store와 같은 값을 쓴다. import하지 않는 것은 이 모듈을 순수하게 두기
# 위해서고, 두 곳이 어긋나면 test_map_upload가 잡는다.
UPLOAD_STATE_PENDING = 'UPLOAD_PENDING'
UPLOAD_STATE_AVAILABLE = 'AVAILABLE'
UPLOAD_STATE_FAILED = 'UPLOAD_FAILED'

# slam_toolbox가 만드는 두 파일. pgm은 이미지, yaml은 해상도·원점이다.
# 둘 다 없으면 관제가 좌표를 지도에 얹을 수 없다.
PGM_CONTENT_TYPE = 'image/x-portable-graymap'
YAML_CONTENT_TYPE = 'application/x-yaml'

_CHUNK_BYTES = 1 << 20


@dataclass
class AttemptState:
    """한 임무 지도의 재시도 상태.

    `permanent`는 4xx처럼 다시 보내도 같은 결과인 실패다. 무한 재시도는 32-3이
    금지한다 — 망이 살아 있는데 계약이 틀린 경우 로그만 채우고 배터리를 쓴다.
    """

    failures: int = 0
    permanent: bool = False
    next_attempt_at: float = 0.0
    last_reason: str = ''


@dataclass
class UploadPlan:
    """올릴 것 한 벌. pgm과 yaml은 항상 함께 간다."""

    mission_id: str | None
    directory: Path
    pgm: Path
    yaml: Path
    pgm_sha256: str = ''
    yaml_sha256: str = ''
    attempts: AttemptState = field(default_factory=AttemptState)


def sha256_of(path: Path) -> str:
    """파일 해시. 한 번에 읽지 않는다.

    지도 pgm은 보통 수백 KB지만 넓은 공간을 오래 돌면 커진다. 젯슨 8GB에서
    다른 노드를 압박하지 않도록 청크로 읽는다.

    백엔드 계약(MapUploadRequest)은 sha256을 받지 않는다. 그래도 계산해
    report.json에 남긴다 — 나중에 객체가 깨진 것으로 의심될 때 우리 쪽에
    비교할 값이 없으면 확인할 방법이 없다.
    """
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def needs_upload(report: dict[str, Any] | None) -> bool:
    """이 지도를 올려야 하는가.

    보고서가 없으면 올린다. 저장은 됐는데 보고서 쓰기 직전에 죽은 경우이고,
    그때 건너뛰면 지도가 영구히 로컬에만 남는다.

    AVAILABLE이면 이미 등록됐으므로 건너뛴다. 같은 지도를 두 번 올리면
    maps 행이 둘 생긴다 — S15P11A301-142에서 같은 종류의 중복이 s3_key 유니크
    제약을 깨고 영구 500을 만든 적이 있다.
    """
    if not report:
        return True
    return str(report.get('uploadState', UPLOAD_STATE_PENDING)) != UPLOAD_STATE_AVAILABLE


def backoff_delay(failures: int, schedule: list[float]) -> float:
    """실패 횟수에 대한 대기 시간.

    표를 넘어서면 마지막 값을 유지한다. 지수적으로 늘리지 않는 이유는 데모
    현장에서 Wi-Fi가 돌아왔을 때 몇 분씩 기다리면 안 되기 때문이다.
    """
    if not schedule:
        return 0.0
    if failures <= 0:
        return 0.0
    return schedule[min(failures, len(schedule)) - 1]


def is_due(state: AttemptState, now: float) -> bool:
    """지금 시도해도 되는가."""
    if state.permanent:
        return False
    return now >= state.next_attempt_at


def registered_report(
    report: dict[str, Any],
    *,
    map_id: str,
    pgm_key: str,
    yaml_key: str,
    uploaded_at: str,
    pgm_sha256: str = '',
    yaml_sha256: str = '',
) -> dict[str, Any]:
    """등록 성공을 보고서에 반영한 새 dict.

    `mapId`가 여기 들어가는 것이 이 티켓의 핵심 산출물이다. 13.2의 maps 행
    식별자이고, telemetry·encounter의 mapId가 최종적으로 가리켜야 하는 값이다
    (S15P11A301-170이 `pose.mapId`를 비워 둔 이유).

    원본을 변형하지 않고 새 dict를 만든다. 쓰기가 실패해도 메모리 상태가
    "성공"으로 앞서가지 않게 하려는 것이다.
    """
    updated = dict(report)
    updated['uploadState'] = UPLOAD_STATE_AVAILABLE
    updated['mapId'] = map_id
    updated['uploadedAt'] = uploaded_at
    # 이전 실패 기록을 지운다. 보고서는 현재 상태를 말해야 한다 — AVAILABLE
    # 옆에 lastError가 남아 있으면 읽는 사람이 성공인지 실패인지 헷갈린다.
    # 재시도 이력은 로그에 있다.
    updated.pop('lastError', None)
    keys = dict(updated.get('keys') or {})
    keys['pgm'] = pgm_key
    keys['yaml'] = yaml_key
    updated['keys'] = keys
    if pgm_sha256 or yaml_sha256:
        checksums = dict(updated.get('sha256') or {})
        if pgm_sha256:
            checksums['pgm'] = pgm_sha256
        if yaml_sha256:
            checksums['yaml'] = yaml_sha256
        updated['sha256'] = checksums
    return updated


def failed_report(
    report: dict[str, Any], *, reason: str, failures: int, permanent: bool
) -> dict[str, Any]:
    """실패를 보고서에 남긴 새 dict.

    uploadState를 UPLOAD_FAILED로 바꾸지 않는다 — PENDING으로 남겨야 다음
    기동에서 다시 집는다. 실패는 별 필드에 기록해 사람이 원인을 볼 수 있게만
    한다. permanent가 True여도 마찬가지다. 계약이 고쳐지고 재배포되면 그때는
    성공해야 한다.
    """
    updated = dict(report)
    updated['uploadState'] = UPLOAD_STATE_PENDING
    updated['lastError'] = {
        'reason': reason[:200],
        'failures': failures,
        'permanent': permanent,
    }
    return updated

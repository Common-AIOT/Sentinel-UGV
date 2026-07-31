"""지도 업로드 로직 테스트 (S15P11A301-171).

실기기·백엔드 없이 돈다. 여기서 막으려는 것은 대부분 **현장에서만 나는**
상황이다 — 망 단절, 중복 업로드, 보고서 없이 저장된 지도.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_recorder.map_store import (  # noqa: E402
    MapStore,
    NO_MISSION_DIRNAME,
    write_report,
)
from sentinel_recorder.map_upload import (  # noqa: E402
    UPLOAD_STATE_AVAILABLE,
    UPLOAD_STATE_PENDING,
    AttemptState,
    backoff_delay,
    failed_report,
    is_due,
    needs_upload,
    registered_report,
    sha256_of,
)

MISSION = '11111111-2222-3333-4444-555555555555'
BACKOFF = [5.0, 15.0, 60.0, 300.0]


# ----------------------------------------------------------------------
# 올릴지 말지
# ----------------------------------------------------------------------


def test_보고서가_없으면_올린다():
    """저장은 됐는데 보고서 쓰기 직전에 죽은 경우.

    건너뛰면 그 지도가 영구히 로컬에만 남는다.
    """
    assert needs_upload(None)
    assert needs_upload({})


def test_PENDING이면_올린다():
    assert needs_upload({'uploadState': UPLOAD_STATE_PENDING})


def test_AVAILABLE이면_건너뛴다():
    """중복 업로드는 maps 행을 둘 만든다.

    S15P11A301-142에서 같은 종류의 중복이 s3_key 유니크 제약을 깨고 업로드를
    영구히 500으로 실패시킨 적이 있다.
    """
    assert not needs_upload({'uploadState': UPLOAD_STATE_AVAILABLE})


def test_실패_기록이_있어도_PENDING이면_다시_올린다():
    report = failed_report(
        {'uploadState': UPLOAD_STATE_PENDING},
        reason='PRESIGN_UNREACHABLE: 연결 없음',
        failures=3,
        permanent=False,
    )
    assert needs_upload(report)


def test_영구_실패도_상태는_PENDING으로_남는다():
    """계약이 고쳐지고 재배포되면 그때는 성공해야 한다."""
    report = failed_report({}, reason='PRESIGN_REJECTED: 400', failures=1, permanent=True)
    assert report['uploadState'] == UPLOAD_STATE_PENDING
    assert report['lastError']['permanent'] is True
    assert needs_upload(report)


# ----------------------------------------------------------------------
# 재시도
# ----------------------------------------------------------------------


def test_백오프가_표를_따른다():
    assert backoff_delay(1, BACKOFF) == 5.0
    assert backoff_delay(2, BACKOFF) == 15.0
    assert backoff_delay(4, BACKOFF) == 300.0


def test_표를_넘으면_마지막_값을_유지한다():
    """무한히 커지면 Wi-Fi가 돌아와도 몇 시간 기다린다."""
    assert backoff_delay(9, BACKOFF) == 300.0


def test_실패가_없으면_즉시():
    assert backoff_delay(0, BACKOFF) == 0.0
    assert backoff_delay(0, []) == 0.0


def test_영구_실패는_시간이_지나도_시도하지_않는다():
    state = AttemptState(failures=1, permanent=True, next_attempt_at=0.0)
    assert not is_due(state, now=10_000.0)


def test_대기_시간_전에는_시도하지_않는다():
    state = AttemptState(failures=1, next_attempt_at=100.0)
    assert not is_due(state, now=99.0)
    assert is_due(state, now=100.0)


# ----------------------------------------------------------------------
# 보고서 갱신
# ----------------------------------------------------------------------


def test_등록되면_mapId와_키가_기록된다():
    """mapId가 이 티켓의 핵심 산출물이다. 13.2 maps 행의 식별자다."""
    report = registered_report(
        {'schemaVersion': '1.0', 'missionId': MISSION},
        map_id='map-uuid',
        pgm_key='maps/m/map.pgm',
        yaml_key='maps/m/map.yaml',
        uploaded_at='2026-07-31T12:00:00+00:00',
        pgm_sha256='a' * 64,
    )
    assert report['uploadState'] == UPLOAD_STATE_AVAILABLE
    assert report['mapId'] == 'map-uuid'
    assert report['keys'] == {'pgm': 'maps/m/map.pgm', 'yaml': 'maps/m/map.yaml'}
    assert report['sha256']['pgm'] == 'a' * 64
    # 저장 단계가 넣은 값이 사라지지 않는다
    assert report['missionId'] == MISSION


def test_원본_보고서를_변형하지_않는다():
    """쓰기가 실패해도 메모리 상태가 성공으로 앞서가지 않아야 한다."""
    original = {'uploadState': UPLOAD_STATE_PENDING}
    registered_report(
        original,
        map_id='m',
        pgm_key='k',
        yaml_key='k2',
        uploaded_at='t',
    )
    assert original == {'uploadState': UPLOAD_STATE_PENDING}


def test_성공하면_이전_실패_기록을_지운다():
    """AVAILABLE 옆에 lastError가 남으면 성공인지 실패인지 헷갈린다.

    실측에서 망 단절 후 복구했을 때 실제로 그 상태가 나왔다.
    """
    failed = failed_report(
        {'missionId': MISSION},
        reason='PRESIGN_UNREACHABLE: 연결 없음',
        failures=5,
        permanent=False,
    )
    assert 'lastError' in failed
    ok = registered_report(
        failed, map_id='m', pgm_key='k', yaml_key='k2', uploaded_at='t'
    )
    assert ok['uploadState'] == UPLOAD_STATE_AVAILABLE
    assert 'lastError' not in ok


def test_해시가_없으면_필드를_만들지_않는다():
    report = registered_report({}, map_id='m', pgm_key='k', yaml_key='k2', uploaded_at='t')
    assert 'sha256' not in report


# ----------------------------------------------------------------------
# 해시
# ----------------------------------------------------------------------


def test_해시가_내용을_따른다(tmp_path):
    a = tmp_path / 'a.pgm'
    b = tmp_path / 'b.pgm'
    a.write_bytes(b'P5 map data')
    b.write_bytes(b'P5 map data')
    assert sha256_of(a) == sha256_of(b)
    b.write_bytes(b'P5 other')
    assert sha256_of(a) != sha256_of(b)


def test_큰_파일도_청크로_읽는다(tmp_path):
    """한 번에 읽으면 젯슨 8GB에서 다른 노드를 압박한다."""
    import hashlib

    big = tmp_path / 'big.pgm'
    payload = b'x' * (3 * (1 << 20) + 7)
    big.write_bytes(payload)
    assert sha256_of(big) == hashlib.sha256(payload).hexdigest()


# ----------------------------------------------------------------------
# 열거 — 무엇을 올릴 후보로 볼지
# ----------------------------------------------------------------------


def _saved(root: Path, mission: str, *, pgm=b'P5 x', yaml=b'resolution: 0.05'):
    directory = root / mission
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'map.pgm').write_bytes(pgm)
    (directory / 'map.yaml').write_bytes(yaml)
    return directory


def test_no_mission_지도는_후보가_아니다(tmp_path):
    """maps.mission_id가 NOT NULL FK라 등록할 수 없다.

    파일은 남겨 두고 사람이 열어볼 수 있게만 한다.
    """
    _saved(tmp_path, MISSION)
    _saved(tmp_path, NO_MISSION_DIRNAME)
    assert MapStore(tmp_path).iter_missions() == [MISSION]


def test_디렉터리가_없으면_빈_목록(tmp_path):
    assert MapStore(tmp_path / 'missing').iter_missions() == []


def test_파일은_후보가_아니다(tmp_path):
    (tmp_path / 'stray.txt').write_text('x')
    assert MapStore(tmp_path).iter_missions() == []


def test_보고서를_읽는다(tmp_path):
    directory = _saved(tmp_path, MISSION)
    write_report(directory / 'report.json', {'uploadState': UPLOAD_STATE_AVAILABLE})
    assert MapStore(tmp_path).read_report(MISSION)['uploadState'] == UPLOAD_STATE_AVAILABLE


def test_깨진_보고서는_없는_것으로_본다(tmp_path):
    """다시 올리는 쪽으로 기운다 — 못 올리는 것이 더 나쁘다."""
    directory = _saved(tmp_path, MISSION)
    (directory / 'report.json').write_text('{ 깨짐', encoding='utf-8')
    store = MapStore(tmp_path)
    assert store.read_report(MISSION) is None
    assert needs_upload(store.read_report(MISSION))


def test_yaml이_없으면_불완전이다(tmp_path):
    """yaml에 해상도와 원점이 있어야 관제가 좌표를 얹는다."""
    directory = tmp_path / MISSION
    directory.mkdir(parents=True)
    (directory / 'map.pgm').write_bytes(b'P5 x')
    saved = MapStore(tmp_path).scan(MISSION)
    assert saved is not None
    assert not saved.complete


def test_보고서_쓰기가_원자적이다(tmp_path):
    """업로더가 읽는 중에 반쪽 파일을 보면 안 된다."""
    path = tmp_path / 'report.json'
    write_report(path, {'a': 1})
    write_report(path, {'a': 2})
    assert json.loads(path.read_text(encoding='utf-8')) == {'a': 2}
    assert list(tmp_path.iterdir()) == [path]

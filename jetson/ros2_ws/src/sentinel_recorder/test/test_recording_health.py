"""마감 결과 상태 시험 (S15P11A301-309).

여기서 지키는 성질은 하나다 — **간헐 실패가 성공에 덮이지 않는다.**

S15P11A301-304의 PTS 동률 결함이 그 반례였다. 같은 임무에서 세 번 중 두 번 영상을
잃었고 한 번은 정상이었다. 「마지막 결과」만 들고 있으면 그 정상 이벤트가 직전
실패를 덮어 화면이 정상으로 보인다. 실제로 19건이 쌓이는 동안 드러나지 않았다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_recorder.recording_health import FinalizeHealth  # noqa: E402


def test_starts_unknown_not_healthy():
    """기동 직후는 「정상」이 아니라 「모름」이다.

    False로 시작하면 아직 아무 이벤트도 없는 상태가 장애로 보이고, True로 시작하면
    실패한 적 없다고 주장하게 된다. 둘 다 사실이 아니다.
    """
    health = FinalizeHealth()

    assert health.as_status() == {'lastFinalizeOk': None, 'lastFailure': None}


def test_failure_is_reported_with_media_state_string():
    """사유는 report.json의 mediaState 값 그대로 나간다.

    관제에서 젯슨 보고서와 대조하므로 문자열이 같아야 한다.
    """
    health = FinalizeHealth()

    health.note_failure('RECORDING_FAILED_PTS_REGRESSION')

    assert health.as_status() == {
        'lastFinalizeOk': False,
        'lastFailure': 'RECORDING_FAILED_PTS_REGRESSION',
    }


def test_success_after_failure_keeps_the_reason():
    """이 시험이 이 모듈의 존재 이유다.

    성공은 「지금 정상」만 되돌리고 「이번 기동에 실패가 있었다」는 남긴다.
    """
    health = FinalizeHealth()
    health.note_failure('RECORDING_FAILED_PTS_REGRESSION')

    health.note_success()

    assert health.as_status() == {
        'lastFinalizeOk': True,
        'lastFailure': 'RECORDING_FAILED_PTS_REGRESSION',
    }


def test_intermittent_failure_survives_alternating_events():
    """304 실측 패턴 그대로 — 실패·실패·성공."""
    health = FinalizeHealth()

    health.note_failure('RECORDING_FAILED_PTS_REGRESSION')
    health.note_failure('RECORDING_FAILED_PTS_REGRESSION')
    health.note_success()

    status = health.as_status()
    assert status['lastFinalizeOk'] is True
    assert status['lastFailure'] == 'RECORDING_FAILED_PTS_REGRESSION'


def test_later_failure_overwrites_earlier_reason():
    """사유는 마지막 것을 보여 준다. 누적 목록은 두지 않는다.

    화면에 한 줄만 들어가고, 전체 이력은 pending의 report.json이 갖는다.
    """
    health = FinalizeHealth()
    health.note_failure('RECORDING_FAILED_PTS_REGRESSION')

    health.note_failure('RECORDING_FAILED_DISK_FULL')

    assert health.as_status()['lastFailure'] == 'RECORDING_FAILED_DISK_FULL'


def test_boot_recovery_does_not_claim_this_boot_finalized():
    """부팅 복구가 찾은 잔해는 지난 기동의 것이다.

    사유는 남기되 「이번 기동의 마지막 마감 결과」는 모름으로 둔다. 여기서 False를
    쓰면, 이번 기동에서 아직 아무 이벤트도 마감하지 않았다는 사실이 가려진다.
    """
    health = FinalizeHealth()

    health.note_failure('CORRUPT', finalized_now=False)

    assert health.as_status() == {
        'lastFinalizeOk': None,
        'lastFailure': 'CORRUPT',
    }


def test_boot_recovery_reason_survives_first_success():
    """복구된 잔해도 성공 한 번에 지워지지 않는다."""
    health = FinalizeHealth()
    health.note_failure('CORRUPT', finalized_now=False)

    health.note_success()

    assert health.as_status() == {
        'lastFinalizeOk': True,
        'lastFailure': 'CORRUPT',
    }

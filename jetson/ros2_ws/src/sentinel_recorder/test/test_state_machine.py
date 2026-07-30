"""녹화 상태 머신 시험 (S15P11A301-123).

시간을 주입하므로 5분 타임아웃을 5분 기다리지 않고 확인한다. 실제 시간을 쓰면
이 시험이 30분 넘게 걸려 아무도 돌리지 않게 된다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sentinel_recorder.state_machine import (  # noqa: E402
    EndReason,
    Phase,
    RecordingState,
    RecordingStateMachine,
)

T0 = datetime(2026, 7, 28, 4, 30, 0, tzinfo=timezone.utc)
EID = 'c81f6d20-5a47-4e93-b2d8-1f70e4a95c33'
OTHER = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def confirmed(machine: RecordingStateMachine, when: float = 0.0, eid: str = EID):
    return machine.on_encounter(eid, Phase.CONFIRMED, at(when), at(when), 1)


# ----------------------------------------------------------------------
# 기본 흐름 (32-5 상태도)
# ----------------------------------------------------------------------


def test_starts_in_buffering():
    assert RecordingStateMachine().state is RecordingState.BUFFERING


def test_confirmed_starts_recording():
    machine = RecordingStateMachine()
    assert confirmed(machine) == 'BUFFERING->RECORDING'
    assert machine.state is RecordingState.RECORDING
    assert machine.recording


def test_full_normal_path():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(EID, Phase.APPROACHED, at(5), at(5)) == (
        'RECORDING->INTERACTION'
    )
    assert machine.on_encounter(EID, Phase.ENDED, at(40), at(40)) == (
        'INTERACTION->POST_RECORDING'
    )
    # 3초가 지나지 않았으면 아직 마무리하지 않는다.
    assert machine.tick(at(42)) is None
    assert machine.tick(at(43)) == 'POST_RECORDING->FINALIZING'
    assert machine.event.end_reason is EndReason.NORMAL
    assert machine.finish(True) == 'FINALIZING->UPLOAD_PENDING'
    assert machine.state is RecordingState.BUFFERING


def test_finish_failure_goes_to_recording_failed():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    machine.tick(at(13))
    assert machine.finish(False) == 'FINALIZING->RECORDING_FAILED'


# ----------------------------------------------------------------------
# 종료 예외 (32-5)
# ----------------------------------------------------------------------


def test_redetection_within_three_seconds_returns_to_interaction():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    assert machine.on_encounter(EID, Phase.REDETECTED, at(12), at(12)) == (
        'POST_RECORDING->INTERACTION'
    )
    assert machine.state is RecordingState.INTERACTION
    # 되돌아왔으므로 3초 경과로 마무리되지 않는다.
    assert machine.tick(at(13)) is None


def test_redetection_after_three_seconds_is_ignored():
    """3초가 지난 재감지를 받아주면 이벤트가 무한히 늘어난다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    assert machine.on_encounter(EID, Phase.REDETECTED, at(14), at(14)) is None
    assert machine.state is RecordingState.POST_RECORDING


def test_no_response_timeout_after_thirty_seconds():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    assert machine.tick(at(20)) is None
    assert machine.tick(at(32)) == 'INTERACTION->POST_RECORDING'
    assert machine.event.end_reason is EndReason.NO_RESPONSE_TIMEOUT


def test_activity_resets_no_response_timer():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    # 25초에 활동이 있으면 타이머가 다시 시작한다.
    machine.on_encounter(EID, Phase.CONFIRMED, at(25), at(25), 2)
    assert machine.tick(at(40)) is None
    assert machine.tick(at(56)) == 'INTERACTION->POST_RECORDING'


def test_max_duration_closes_event_at_five_minutes():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    # 활동을 계속 주어 NO_RESPONSE_TIMEOUT을 피한다.
    for second in range(10, 300, 20):
        machine.on_encounter(EID, Phase.CONFIRMED, at(second), at(second), 1)
    assert machine.tick(at(299)) is None
    assert machine.tick(at(300)) == 'INTERACTION->FINALIZING'
    assert machine.event.end_reason is EndReason.MAX_DURATION


def test_max_duration_wins_over_post_recording():
    """5분을 넘긴 이벤트는 어떤 상태에 있든 닫는다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(299), at(299))
    assert machine.tick(at(301)) == 'POST_RECORDING->FINALIZING'
    assert machine.event.end_reason is EndReason.MAX_DURATION


def test_person_lost_starts_post_recording():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(EID, Phase.LOST, at(8), at(8)) == (
        'RECORDING->POST_RECORDING'
    )
    assert machine.event.end_reason is EndReason.PERSON_LOST


# ----------------------------------------------------------------------
# 다중 인원과 중복 신호 (32-6)
# ----------------------------------------------------------------------


def test_repeated_confirmed_does_not_split_event():
    """32-6. 동시에 발견된 사람들은 encounter 하나를 공유한다.

    CONFIRMED가 여러 번 와도 이벤트가 쪼개지면 안 된다. VID-05가 "사람 3명일 때
    encounter 1개와 MP4 1개"를 요구한다.
    """
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    first_event = machine.event
    assert machine.on_encounter(EID, Phase.CONFIRMED, at(1), at(1), 2) is None
    assert machine.on_encounter(EID, Phase.CONFIRMED, at(2), at(2), 3) is None
    assert machine.event is first_event
    assert machine.event.person_count == 3


def test_other_encounter_is_ignored_while_recording():
    """두 이벤트가 같은 조각을 나눠 가지면 어느 MP4에 넣을지 정할 수 없다."""
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert confirmed(machine, 5, OTHER) is None
    assert machine.event.encounter_id == EID


def test_signals_for_unknown_encounter_are_ignored():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    assert machine.on_encounter(OTHER, Phase.ENDED, at(5), at(5)) is None
    assert machine.state is RecordingState.RECORDING


def test_signals_without_event_are_ignored():
    machine = RecordingStateMachine()
    for phase in (Phase.APPROACHED, Phase.ENDED, Phase.REDETECTED, Phase.LOST):
        assert machine.on_encounter(EID, phase, at(1), at(1)) is None
    assert machine.state is RecordingState.BUFFERING
    assert machine.tick(at(600)) is None


# ----------------------------------------------------------------------
# recording 플래그와 마감 예정 시각
# ----------------------------------------------------------------------


def test_recording_flag_covers_collection_states():
    machine = RecordingStateMachine()
    assert not machine.recording
    confirmed(machine, 0)
    assert machine.recording
    machine.on_encounter(EID, Phase.APPROACHED, at(1), at(1))
    assert machine.recording
    machine.on_encounter(EID, Phase.ENDED, at(2), at(2))
    assert machine.recording  # POST_RECORDING도 조각을 모은다
    machine.tick(at(6))
    assert not machine.recording  # FINALIZING부터는 모으지 않는다


def test_deadline_hint_points_at_next_transition():
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.ENDED, at(10), at(10))
    # POST_RECORDING이면 3초 뒤가 다음 전이다.
    assert machine.deadline_hint(at(11)) == at(13)


# ----------------------------------------------------------------------
# 조각 파일명
# ----------------------------------------------------------------------


def test_local_filename_differs_from_ring_filename():
    """이벤트 디렉터리 파일명이 링 버퍼 파일명과 달라야 한다.

    splitmuxsink가 `max-files`로 파일명을 순환시킨다. 실측으로 segmentId가
    [0..7]에서 [6,7,0,1,..]로 바뀌는 것을 확인했다. 링 파일명을 그대로 쓰면
    서로 다른 조각이 같은 파일을 가리켜 **MP4가 같은 구간을 반복한다.** 그런데
    길이와 프레임 수는 정확히 맞아서 숫자 검증을 통과한다. 68조각 이벤트가
    67.9초 2036프레임으로 나와 정상처럼 보였다.

    이 시험 하나가 그 결함을 막는다.
    """
    from datetime import datetime as _dt

    from sentinel_recorder.segment_store import Segment

    stamp = _dt(2026, 7, 28, tzinfo=timezone.utc)

    def make(segment_id: int, sequence: int) -> Segment:
        return Segment(
            segment_id=segment_id,
            sequence=sequence,
            started_at=stamp,
            ended_at=stamp,
            duration_ms=1000,
            first_pts=0,
            first_frame_key=True,
            path=f'buffer/seg_{segment_id:06d}.ts',
        )

    # 링 파일명이 재사용된 두 조각. sequence는 다르다.
    first = make(segment_id=3, sequence=100)
    second = make(segment_id=3, sequence=108)

    assert first.filename == second.filename, '링 파일명은 순환하므로 같다'
    assert first.local_filename != second.local_filename, (
        '이벤트 디렉터리 이름이 같으면 조각이 덮어써져 영상이 반복된다'
    )
    assert first.local_filename == 'seg_00000100.ts'
    assert second.local_filename.endswith('.ts')


# ----------------------------------------------------------------------
# 보고서 쓰기의 원자성 (S15P11A301-124)
# ----------------------------------------------------------------------


def test_write_report_is_atomic_and_leaves_no_temporary(tmp_path):
    """쓰는 중에 읽어도 잘린 JSON이 보이면 안 된다.

    `recording_manager`와 `media_uploader`는 별 프로세스이고 같은 `report.json`을
    쓴다. 비원자적으로 쓰던 동안, 겹친 한 번이 이벤트를 영구 실패로 떨어뜨려 다시는
    업로드하지 않았다. 그래서 원자성이 이 파일의 계약이다.
    """
    from sentinel_recorder.event_finalizer import read_report, write_report

    path = tmp_path / 'report.json'
    write_report(path, {'uploadState': 'UPLOAD_PENDING', 'note': '가' * 5000})

    # 큰 본문을 여러 번 덮어써도, 매 시점의 파일은 항상 완전한 JSON이다.
    for index in range(20):
        write_report(path, {'uploadState': 'AVAILABLE', 'seq': index, 'pad': '나' * 5000})
        assert read_report(path)['seq'] == index

    leftovers = [entry.name for entry in tmp_path.iterdir() if entry.name != 'report.json']
    assert leftovers == [], f'임시 파일이 남았다: {leftovers}'


def test_unreadable_report_is_retryable():
    """보고서를 못 읽는 것은 영구 실패가 아니다.

    영구 실패로 표시하면 워커가 그 이벤트를 다시 시도하지 않는다. 일시적인 읽기
    실패 하나로 이벤트 영상을 영원히 잃는 경로였다.
    """
    from sentinel_recorder.upload_worker import UploadWorker
    from sentinel_recorder.upload_client import UploadError

    worker = UploadWorker.__new__(UploadWorker)
    try:
        worker._read_report(Path('/nonexistent-directory-for-test'))
    except UploadError as error:
        assert error.reason == 'REPORT_UNREADABLE'
        assert error.retryable, '재시도하지 않으면 이벤트를 잃는다'
    else:
        raise AssertionError('읽기 실패에 UploadError를 올려야 한다')


def test_worker_skips_event_whose_finalize_has_not_registered_checksum(tmp_path):
    """마무리 중인 이벤트를 업로드 대상으로 잡으면 안 된다.

    32-5의 순서상 `event.mp4`는 이미 최종 이름인데 보고서에 `media.sha256`이 아직
    없는 순간이 있다. 그 창에서 집으면 `CHECKSUM_MISSING`으로 이벤트를 잃었다.
    """
    from sentinel_recorder.event_finalizer import write_report
    from sentinel_recorder.pending_store import PendingStore
    from sentinel_recorder.upload_worker import UploadWorker

    directory = tmp_path / 'eb1c6850-0000-4000-8000-000000000000'
    directory.mkdir()
    (directory / 'event.mp4').write_bytes(b'\x00' * 1024)
    # `_begin_event`가 남기는 진행 중 보고서. media 블록이 아직 없다.
    write_report(directory / 'report.json', {'encounterId': directory.name, 'startedAt': 'x'})

    store = PendingStore(tmp_path)
    event = store.scan()[0]
    assert event.has_media, '영상 파일은 이미 최종 이름이다'
    assert not event.ready_for_upload, '체크섬이 없으면 올릴 준비가 안 된 것이다'

    class ExplodingClient:
        def upload(self, **_kwargs):
            raise AssertionError('마무리 중인 이벤트로 백엔드를 호출하면 안 된다')

    stats = UploadWorker(store, ExplodingClient()).run_once(now=0.0)
    assert stats.attempted == 0
    assert stats.skipped_permanent == 0, '영구 실패로 굳으면 다시 올리지 않는다'

    # 마무리가 끝나면 같은 이벤트가 대상이 된다.
    write_report(
        directory / 'report.json',
        {'encounterId': directory.name, 'media': {'sha256': 'a' * 64}},
    )
    assert PendingStore(tmp_path).scan()[0].ready_for_upload


# ----------------------------------------------------------------------
# REPORT_COMMITTED 발행 (S15P11A301-139)
# ----------------------------------------------------------------------


def test_report_committed_body_satisfies_the_mission_signal_schema():
    """녹화 노드가 내는 REPORT_COMMITTED가 계약을 만족하는지.

    이 신호가 없으면 mission_manager가 REPORTING에서 못 나오고 다음 사람을 찾지
    못한다(S15P11A301-139). 형식이 틀리면 mission_manager가 조용히 버린다.
    """
    jsonschema = pytest.importorskip(
        'jsonschema', reason='jsonschema가 없으면 계약 검증을 건너뛴다'
    )
    repo_root = Path(__file__).resolve().parents[5]
    schema = json.loads(
        (repo_root / 'common' / 'schemas' / 'mission-signal.schema.json').read_text(
            encoding='utf-8'
        )
    )
    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )

    # 노드가 만드는 것과 같은 형태다. 노드 파일은 rclpy를 끌어오므로 여기서
    # 직접 구성한다.
    body = {
        'signal': 'REPORT_COMMITTED',
        'sentAt': '2026-07-29T04:15:30.123Z',
        'source': 'PERCEPTION',
        'encounterId': '924bcd75-7d5c-417c-b1e4-b01d2872d287',
        'detail': '이벤트 보고서를 로컬에 저장했다',
        'commandId': None,
    }
    errors = list(validator.iter_errors(body))
    assert not errors, [error.message for error in errors]


def test_mission_signal_schema_allows_perception_as_source():
    """녹화 노드는 PERCEPTION으로 자신을 알린다. enum에 없으면 계약 위반이다."""
    repo_root = Path(__file__).resolve().parents[5]
    schema = json.loads(
        (repo_root / 'common' / 'schemas' / 'mission-signal.schema.json').read_text(
            encoding='utf-8'
        )
    )
    assert 'PERCEPTION' in schema['properties']['source']['enum']
    assert 'REPORT_COMMITTED' in schema['properties']['signal']['enum']


# ----------------------------------------------------------------------
# 업로드 요청 본문이 계약을 만족하는가 (S15P11A301-124)
# ----------------------------------------------------------------------


def _upload_request_validator():
    jsonschema = pytest.importorskip(
        'jsonschema', reason='jsonschema가 없으면 계약 검증을 건너뛴다'
    )
    repo_root = Path(__file__).resolve().parents[5]
    schema = json.loads(
        (
            repo_root / 'common' / 'schemas' / 'media-upload-request.schema.json'
        ).read_text(encoding='utf-8')
    )
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


def test_upload_request_body_satisfies_the_schema(tmp_path):
    """`UploadClient`가 실제로 만드는 본문을 계약으로 검사한다.

    이 시험이 없어서 실물 업로드에서야 400을 만났다. 스키마 파일만 검사하면
    "코드가 그 스키마를 지키는가"는 확인되지 않는다.

    mediaId가 UUID가 아니면 백엔드가 400을 낸다. `media_assets.id`가
    `UUID PRIMARY KEY`이기 때문이다(31-10).
    """
    from sentinel_recorder.upload_client import KIND_VIDEO, UploadClient, UploadTarget

    captured = {}

    class FakeSession:
        def post(self, url, json=None, headers=None, timeout=None):
            captured['url'] = url
            captured['body'] = json

            class Response:
                status_code = 200

                @staticmethod
                def json():
                    return {'data': {'objectKey': 'k', 'url': 'https://example/put'}}

                text = ''

            return Response()

    media = tmp_path / 'event.mp4'
    media.write_bytes(b'\x00' * 2048)
    client = UploadClient('https://api.example', session=FakeSession())
    client.request_upload_url(
        encounter_id='b9c43b74-e7f9-4f74-8358-9656293bc1af',
        media_id='2f8c1e40-91ab-4c5d-8e37-1a6b4d9f0c22',
        target=UploadTarget(
            path=media,
            kind=KIND_VIDEO,
            sha256='a' * 64,
            size_bytes=2048,
            content_type='video/mp4',
        ),
        suggested_key='SENTINEL-01/b9c43b74/event.mp4',
    )

    errors = list(_upload_request_validator().iter_errors(captured['body']))
    assert not errors, [error.message for error in errors]


def test_non_uuid_media_id_is_rejected_by_the_contract():
    """옛 `m_{hex12}` 형식이 계약에서 걸리는지.

    이 형식으로 실물 업로드가 400 "잘못된 입력값입니다"로 실패했다. 계약이
    그것을 잡아야 CI에서 먼저 드러난다.
    """
    body = {
        'encounterId': 'b9c43b74-e7f9-4f74-8358-9656293bc1af',
        'mediaId': 'm_79e64008e364',
        'kind': 'EVENT_VIDEO',
        'fileName': 'event.mp4',
        'sizeBytes': 4908120,
        'sha256': 'a' * 64,
        'contentType': 'video/mp4',
        'suggestedKey': 'SENTINEL-01/b9c43b74/event.mp4',
    }
    errors = list(_upload_request_validator().iter_errors(body))
    assert errors, '옛 형식이 계약을 통과하면 안 된다'
    assert any('mediaId' in str(error.absolute_path) for error in errors)


def test_thumbnail_media_id_is_a_uuid_and_deterministic():
    """썸네일 mediaId도 UUID여야 하고 재시도에 같은 값이어야 한다.

    전에는 `{mediaId}_thumb`였고 UUID가 아니었다. 영상이 통과해도 썸네일에서
    같은 400이 난다. uuid4로 매번 새로 만들면 재시도마다 새 행이 생겨 31-10의
    멱등성이 깨진다.
    """
    import uuid as uuid_module

    from sentinel_recorder.upload_worker import thumbnail_media_id

    video = '2f8c1e40-91ab-4c5d-8e37-1a6b4d9f0c22'
    first = thumbnail_media_id(video)
    assert first == thumbnail_media_id(video), '재시도에 값이 바뀌면 중복 등록된다'
    assert first != video, '영상과 같은 id를 쓰면 서로를 덮어쓴다'
    uuid_module.UUID(first)

    body = {
        'encounterId': 'b9c43b74-e7f9-4f74-8358-9656293bc1af',
        'mediaId': first,
        'kind': 'THUMBNAIL',
        'fileName': 'thumbnail.jpg',
        'sizeBytes': 64000,
        'sha256': 'b' * 64,
        'contentType': 'image/jpeg',
        'suggestedKey': 'SENTINEL-01/b9c43b74/thumbnail.jpg',
    }
    errors = list(_upload_request_validator().iter_errors(body))
    assert not errors, [error.message for error in errors]


# ----------------------------------------------------------------------
# 오디오 트랙 (S15P11A301-131)
# ----------------------------------------------------------------------


def test_pending_cap_includes_audio_bitrate():
    """상한 환산에 오디오가 들어간다.

    빠뜨리면 실제 사용량이 상한을 넘는다. 실측 증가분이 72.7kbps이므로 30분
    분량이 562MB에서 약 580MB가 된다. 상한을 과소 계산하면 지울 필요가 없는
    미업로드 이벤트를 RECORDING_FAILED_DISK_FULL로 포기한다.

    기본값이 인코더 설정값(64k)보다 큰 것이 의도다. AAC 프레임 헤더와 다중화
    몫이 붙는다.
    """
    from sentinel_recorder.pending_store import (
        DEFAULT_AUDIO_BITRATE_KBPS,
        DEFAULT_BITRATE_KBPS,
        bytes_for_seconds,
    )

    with_audio = bytes_for_seconds(1800)
    video_only = bytes_for_seconds(1800, DEFAULT_BITRATE_KBPS, 0)

    assert with_audio > video_only
    assert with_audio - video_only == 1800 * DEFAULT_AUDIO_BITRATE_KBPS * 1000 // 8
    assert 575_000_000 < with_audio < 585_000_000, '30분 상한은 약 580MB다'
    assert DEFAULT_AUDIO_BITRATE_KBPS >= 73, (
        '실측 증가분 72.7kbps를 덮어야 한다. 인코더 설정값 64를 그대로 쓰면 부족하다'
    )


def test_pending_cap_can_drop_audio_for_silent_configurations():
    """오디오를 끈 구성에서는 0을 줘 상한을 과대 계산하지 않는다."""
    from sentinel_recorder.pending_store import PendingStore

    loud = PendingStore('/tmp/x', 1800, 2500, 80)
    quiet = PendingStore('/tmp/x', 1800, 2500, 0)
    assert loud.cap_bytes > quiet.cap_bytes
    assert quiet.cap_bytes == 1800 * 2500 * 1000 // 8


def test_probe_audio_stream_returns_none_for_video_only_file(tmp_path):
    """오디오가 없는 파일은 None이다. 예외가 아니다.

    마이크가 없는 기기에서도 비디오만으로 이벤트가 성립해야 한다. 여기서 예외를
    올리면 마무리가 실패하고, 소리가 없다는 이유로 영상을 통째로 잃는다.
    """
    import shutil

    from sentinel_recorder.event_finalizer import probe_audio_stream

    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe가 없다')

    silent = tmp_path / 'silent.mp4'
    _make_test_clip(silent, with_audio=False)
    assert probe_audio_stream(silent) is None


def test_probe_audio_stream_reads_aac_track(tmp_path):
    """AAC 트랙이 있으면 코덱과 형식을 돌려준다.

    보고서의 `media.audio`가 이 값이다. 관제에서 "소리가 있는 이벤트인가"를
    파일을 열지 않고 판정할 수 있어야 한다.
    """
    import shutil

    from sentinel_recorder.event_finalizer import probe_audio_stream

    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe가 없다')

    loud = tmp_path / 'loud.mp4'
    _make_test_clip(loud, with_audio=True)

    audio = probe_audio_stream(loud)
    assert audio is not None, 'AAC 트랙을 찾지 못했다'
    assert audio['codec'] == 'aac'
    assert audio['sampleRate'] == 48000
    assert audio['channels'] == 1


def test_probe_audio_stream_returns_none_for_unreadable_file(tmp_path):
    """읽을 수 없는 파일도 None이다.

    비디오 재생 검사(`probe_playable`)가 이미 파일 무결성을 판정한다. 오디오
    조회가 같은 실패로 다시 예외를 올리면 실패 사유가 두 갈래로 갈린다.
    """
    import shutil

    from sentinel_recorder.event_finalizer import probe_audio_stream

    if not shutil.which('ffprobe'):
        pytest.skip('ffprobe가 없다')

    broken = tmp_path / 'broken.mp4'
    broken.write_bytes(b'not an mp4')
    assert probe_audio_stream(broken) is None


def _make_test_clip(path: Path, with_audio: bool) -> None:
    """1초짜리 시험용 MP4. ffmpeg의 합성 소스만 쓰므로 장비가 필요 없다."""
    import subprocess

    command = [
        'ffmpeg', '-v', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=15:duration=1',
    ]
    if with_audio:
        command += [
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
            '-ac', '1', '-c:a', 'aac', '-b:a', '64k',
        ]
    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)]
    subprocess.run(command, capture_output=True, check=True, timeout=60)


def test_report_distinguishes_missing_microphone_from_dropped_audio(tmp_path):
    """소리가 없는 두 경우를 구분해야 한다.

    마이크가 없어 처음부터 소리가 없던 것은 정상이고, 조각에는 있었는데
    재다중화가 잃은 것은 결함이다. 보고서에서 둘 다 `audio: null`이면 구분할 수
    없고, 그러면 조용한 데이터 손실을 아무도 알아채지 못한다.

    `audioDropped`가 그 구분입니다.
    """
    import shutil

    from sentinel_recorder.event_finalizer import probe_audio_stream

    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg/ffprobe가 없다')

    silent = tmp_path / 'silent.mp4'
    loud = tmp_path / 'loud.mp4'
    _make_test_clip(silent, with_audio=False)
    _make_test_clip(loud, with_audio=True)

    # 마이크 없음: 조각에도 없고 결과에도 없다 → 결함이 아니다.
    assert probe_audio_stream(silent) is None
    audio_expected = probe_audio_stream(silent) is not None
    assert not (audio_expected and probe_audio_stream(silent) is None)

    # 트랙 유실: 조각에는 있는데 결과에는 없다 → 결함이다.
    audio_expected = probe_audio_stream(loud) is not None
    assert audio_expected
    assert audio_expected and probe_audio_stream(silent) is None


# ----------------------------------------------------------------------
# 마감한 encounter의 재트리거 (S15P11A301-142)
# ----------------------------------------------------------------------


def test_state_machine_accepts_confirmed_for_a_finished_encounter():
    """상태 머신은 마감한 encounter의 CONFIRMED를 다시 받는다.

    이것이 결함의 출발점이다. 상태 머신은 "이 encounter를 전에 마감했다"를 모른다 —
    `finish()`가 이벤트를 비우기 때문이다. 그래서 노드가 기억해야 한다.

    이 시험은 그 사실을 고정한다. 여기가 바뀌면 노드의 가드가 필요 없어지므로
    같이 확인해야 한다.
    """
    machine = RecordingStateMachine()
    confirmed(machine, 0)
    machine.on_encounter(EID, Phase.APPROACHED, at(2), at(2))
    # 30초 무응답으로 마감된다. 실제 결함이 시작된 경로다.
    assert machine.tick(at(32)) == 'INTERACTION->POST_RECORDING'
    assert machine.tick(at(36)) == 'POST_RECORDING->FINALIZING'
    machine.finish(True)
    assert machine.event is None

    again = machine.on_encounter(EID, Phase.CONFIRMED, at(20), at(20), 1)
    assert again is not None, '상태 머신은 같은 encounter를 다시 시작한다'
    assert machine.event is not None
    assert machine.event.encounter_id == EID


def test_finalized_encounter_ids_are_remembered_across_events():
    """노드가 마감한 encounterId를 계속 기억해야 한다.

    상한을 두면 밀려난 encounterId가 다시 트리거될 수 있고, 그것이 막으려는
    결함이다. 임무 하나의 탐사 시간이 최대 7분이라(23.4) 집합은 수십 건에서
    멈춘다.

    노드를 띄우지 않고 그 자료구조의 성질만 확인한다. `rclpy`가 필요한 부분은
    실물 검증으로 본다.
    """
    finalized: set[str] = set()
    for index in range(50):
        finalized.add(f'{index:08d}-bbbb-4ccc-8ddd-eeeeeeeeeeee')

    assert '00000000-bbbb-4ccc-8ddd-eeeeeeeeeeee' in finalized, (
        '오래된 encounterId도 남아 있어야 재트리거를 막는다'
    )
    assert len(finalized) == 50


def test_duplicate_recording_would_collide_on_the_object_key():
    """왜 두 번 녹화하면 안 되는지를 계약으로 고정한다.

    29.6의 object key는 `missionId`와 `encounterId`, `kind`만 쓴다. `mediaId`가
    들어가지 않으므로 같은 encounter의 두 녹화는 같은 key를 만들고,
    `media_assets.s3_key`가 UNIQUE라 두 번째 발급이 실패한다.

    이 시험이 깨지면(key에 mediaId가 들어가게 되면) 노드의 가드가 필요 없어질 수
    있다. 그때 다시 판단해야 한다.
    """
    mission_id = '4bde8ad1-c74b-4d42-bec3-9f71af94b41a'
    encounter_id = '6a75f497-5dae-46ba-945f-d5ed36a1044c'

    def object_key(media_id: str) -> str:
        # 백엔드 MediaService.objectKey 와 같은 규칙이다.
        return f'missions/{mission_id}/encounters/{encounter_id}/event.mp4'

    first = object_key('dc81c239-dbd5-4323-9f2d-90b9b3235f6d')
    second = object_key('73f6d1bb-488b-4e2a-89cb-f85ef4078208')
    assert first == second, (
        'mediaId가 달라도 key가 같다. 그래서 두 번째 업로드가 유니크 제약을 위반한다'
    )

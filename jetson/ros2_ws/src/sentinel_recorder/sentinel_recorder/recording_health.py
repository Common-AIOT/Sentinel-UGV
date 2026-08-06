#!/usr/bin/env python3
"""마감 결과를 관제까지 내보내기 위한 상태 (S15P11A301-309).

## 왜 별 모듈인가

`recording_manager_node`는 `rclpy`를 import하므로 CI에서 돌지 않는다(.gitlab-ci.yml
의 `test:jetson-unit` 주석 참고). 여기서 결정되는 것은 「무엇을 실패로 볼지」와
「언제 지울지」이고, 그 둘이 틀리면 화면이 조용히 거짓말을 한다. 시험을 붙일 수
있는 자리에 둔다.

## 왜 성공이 사유를 지우지 않는가

S15P11A301-304의 PTS 동률 결함은 **간헐적**이었다. 같은 임무에서 세 번 중 두 번
영상을 잃었고, 나머지 한 번은 정상이었다. 성공 한 번에 사유를 지우면 그 정상
이벤트가 직전 실패를 덮는다. 실제로 그 결함은 19건이 쌓이는 동안 드러나지 않았고,
사람이 pending 디렉터리를 직접 열어 보고 나서야 발견됐다.

그래서 값을 둘로 나눈다.

* `last_ok` — 지금 정상인가. 마지막으로 마감한 이벤트가 영상을 남겼는지다
* `last_failure` — 이번 기동에 실패가 있었나. 한 번 실패하면 재기동까지 남는다

한 필드로 합치면 후자를 표현할 수 없다. 「지금 정상」과 「한 번도 실패한 적 없음」은
다른 사실이고, 재발을 알아채는 데 필요한 것은 후자다.

이 선택은 `ENVIRONMENT_SENSOR_FAULT`(S15P11A301-258)와 반대다. 그쪽은 성공 읽기
한 번에 해제한다. DHT11은 센서가 살아 있으면 계속 성공하므로 실패가 이어지는
동안만 의미가 있지만, 마감 실패는 이벤트가 있을 때만 판정되고 이벤트 사이 간격이
몇 분이라 같은 규칙을 쓰면 거의 항상 정상으로 보인다.
"""

from __future__ import annotations


class FinalizeHealth:
    """마지막 마감 결과와 이번 기동의 실패 사유를 들고 있는다."""

    def __init__(self) -> None:
        # 둘 다 None으로 시작한다. 「이번 기동에서 아직 아무 이벤트도 마감하지
        # 않았다」와 「마감했고 정상이다」는 다른 사실이므로 False로 시작하지 않는다.
        self.last_ok: bool | None = None
        self.last_failure: str | None = None

    def note_success(self) -> None:
        """영상을 남기고 마감했다. 사유는 지우지 않는다 — 모듈 주석 참고."""
        self.last_ok = True

    def note_failure(self, media_state: str, finalized_now: bool = True) -> None:
        """마감이 영상을 남기지 못했다.

        `media_state`는 `report.json`에 쓰는 값을 그대로 받는다. 관제에서 젯슨
        보고서와 대조할 때 문자열이 같아야 하므로 여기서 다시 만들지 않는다.

        `finalized_now=False`는 부팅 복구가 지난 기동의 잔해를 발견한 경우다.
        사유는 남기되 `last_ok`는 건드리지 않는다 — 그것은 「이번 기동의 마지막
        마감 결과」이고, 지난 기동의 실패로 False를 만들면 이번 기동에서 아직
        아무것도 마감하지 않았다는 사실이 가려진다.
        """
        self.last_failure = media_state
        if finalized_now:
            self.last_ok = False

    def as_status(self) -> dict[str, bool | str | None]:
        """`recording_manager`의 `~/status`에 실을 형태.

        키 이름이 텔레메트리의 `health.recorderOk`·`recorderLastFailure`와 다른
        이유는, 이 토픽이 노드 상태이고 저쪽이 로봇 건강이기 때문이다. 옮기는 것은
        `cloud_bridge._on_recorder_status`다.
        """
        return {
            'lastFinalizeOk': self.last_ok,
            'lastFailure': self.last_failure,
        }

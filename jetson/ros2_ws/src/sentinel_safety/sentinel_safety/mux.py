"""자율/수동 명령 중재 (S15P11A301-237, 명세 24.1·26.2).

체인의 첫 층이다. 입력이 둘(`/cmd_vel_nav`·`/cmd_vel_manual`)이고 출력이 하나라,
**어느 쪽을 통과시킬지**만 결정한다. rclpy 없이 시험한다.

## 판단을 여기서 다시 하지 않는다

`controlMode` 는 `mission_manager` 가 소유한다. 이 층은 그것을 **읽기만** 한다 —
"수동인데 Nav2 가 명령을 보내니 아마 자율일 것" 같은 추측을 하지 않는다. 두 곳이
모드를 판단하면 어긋나는 순간이 생기고, 그 순간에 두 소스가 동시에 통과한다.

## 모드를 모르면 아무것도 통과시키지 않는다

`controlMode` 가 아직 안 왔거나(`None`) 모르는 값이면 **막는다.** 기본값을 자율로
두면 기동 직후 임무 상태를 받기 전에 Nav2 의 잔여 명령이 통과할 수 있다.

## 수동 경로는 발행자가 **영구히** 없다

폰은 자기 핫스팟 위에서 모터 ESP32 에 직결하고, 젯슨과 관제 PC 는 별개 WiFi 망에
있다. **젯슨은 폰에 도달할 수 없다.** 이것은 확정된 토폴로지이며 열린 문이 아니라
닫힌 문이다(S15P11A301-298). 종전 주석은 "앱이 젯슨을 경유하도록 바뀌면 여기만
채우면 된다" 고 적었는데, 그런 일은 계획에 없다.

액추에이션 중재도 이 층에 있지 않다. 수동과 자율이 같은 모터를 두고 다투는 것을
막는 것은 **모터 ESP32** 다 — 수동 패킷을 받는 순간 래치를 잡고 젯슨의
`DRIVE_COMMAND` 액추에이션을 거부하며, 젯슨은 `DRIVE_STATE.state` 를 따라간다.

그래도 이 층을 남기는 이유는 명세 24.1 이 두 입력을 요구하고, `controlMode` 로
게이팅한다는 규칙 자체가 옳기 때문이다. 수동일 때 `/cmd_vel_nav` 를 막는 것은
지금도 실제로 동작한다.

언젠가 `/cmd_vel_manual` 을 채우는 사람은 **같은 커밋에서
`mission_state.MOVEMENT[MANUAL]` 도 바꿔야 한다.** 지금 그 값이 `(False, None)` 인
것은 자리표시자가 아니라 "젯슨에는 수동 속도 소스가 없다" 는 사실이고, 소스만
만들고 그 표를 놔두면 상위 게이트가 조용히 전부 0 으로 만든다.

**없는 경로를 있는 것처럼 문서에 적지 않는다**(8.2 갭 표에 그대로 남긴다).
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_AUTO = 'auto'
SOURCE_MANUAL = 'manual'
SOURCE_NONE = 'none'

# state.schema.json 의 controlMode 값. 이 셋 밖은 모르는 값으로 다룬다.
MODE_AUTO = 'AUTO'
MODE_MANUAL = 'MANUAL'


@dataclass(frozen=True)
class MuxDecision:
    """무엇을 통과시켰는지와 왜. `source` 가 `none` 이면 0 이 나간다."""

    source: str
    linear_mps: float
    angular_radps: float
    reason: str = ''

    @property
    def passed(self) -> bool:
        return self.source != SOURCE_NONE


def select(
    control_mode: str | None,
    *,
    auto: tuple[float, float] | None,
    manual: tuple[float, float] | None,
) -> MuxDecision:
    """`controlMode` 가 지정한 쪽만 통과시킨다.

    `auto`·`manual` 은 `(linear, angular)` 이며 `None` 은 "그 소스에 유효한 최신
    명령이 없다" 는 뜻이다(TTL 판정은 호출자가 한다 — 시계를 두 곳에 두지 않는다).
    """
    if control_mode == MODE_AUTO:
        if auto is None:
            return MuxDecision(SOURCE_NONE, 0.0, 0.0, 'AUTO 인데 /cmd_vel_nav 가 없다')
        return MuxDecision(SOURCE_AUTO, auto[0], auto[1])

    if control_mode == MODE_MANUAL:
        if manual is None:
            return MuxDecision(
                SOURCE_NONE, 0.0, 0.0,
                'MANUAL 인데 /cmd_vel_manual 이 없다 — 모바일 앱은 젯슨을 '
                '거치지 않으므로 이것이 정상이다',
            )
        return MuxDecision(SOURCE_MANUAL, manual[0], manual[1])

    return MuxDecision(
        SOURCE_NONE, 0.0, 0.0,
        f'controlMode 를 모른다({control_mode!r}) — 기본값을 자율로 두지 않는다',
    )

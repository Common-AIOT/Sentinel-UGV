"""25.2 탐지 확정 규칙 (S15P11A301-136).

ROS도 YOLO도 모른다. track 목록을 받아 "확정된 후보"만 걸러낸다. 시간을 주입하므로
1초 안정성을 1초 기다리지 않고 시험할 수 있다.

## 25.2의 규칙 중 여기서 판정하는 것

    class가 person이다                          detector가 이미 걸렀다
    confidence가 설정값 이상이다                 detector가 이미 걸렀다
    동일 track이 약 1초 동안 최소 관측 횟수 만족   이 모듈
    박스 크기·위치가 비정상적으로 급변하지 않는다   이 모듈
    카메라 timestamp와 TF를 조회할 수 있다        노드 (TF는 S15P11A301-137 이후)
    이미 활성 encounter에 포함된 track인지        mission_manager (25.4)

마지막 항목을 여기서 하지 않는 이유는 활성 encounter를 아는 것이
`mission_manager`뿐이기 때문이다. 그것을 알려면 이 노드가 `/perception/encounter`를
되받아야 하고, 그러면 26.1의 단일 권한이 흐려진다.

## 단일 프레임으로 확정하지 않는 이유

25.2가 "단일 프레임의 박스는 이벤트로 확정하지 않는다"로 시작한다. 오탐 한 프레임이
이벤트 영상을 만들고 관제에 알림을 띄우기 때문이다. 30.6의 장단점 표도 이벤트 기반
저장의 단점으로 "오탐 클립이 생성될 수 있다"를 적었다.

## 급변 검사가 잡는 것

같은 track인데 박스가 갑자기 두 배가 되거나 화면 반대편으로 뛰면, IoU 추적이 다른
물체를 잘못 이어붙인 것이다. 그대로 확정하면 사람이 아닌 것을 사람으로 보고한다.

문턱을 넉넉히 둔다. 사람이 카메라로 다가오면 박스는 실제로 빠르게 커진다. 30.3의
접근 속도가 0.10m/s 이하이므로 로봇 쪽 움직임은 느리지만, 사람이 달려오는 경우를
막을 이유는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tracker import Track

# 25.2의 "약 1초". 이 창 안에서 최소 관측 횟수를 센다.
DEFAULT_WINDOW_SECONDS = 1.0
# 창 안에서 몇 번 봐야 확정인가. 추론 5Hz에서 3이면 0.6초 안에 3번이다.
# 1로 두면 단일 프레임 확정이 되어 25.2를 위반한다.
DEFAULT_MIN_OBSERVATIONS = 3
# 프레임 간 박스 면적이 이 배율을 넘게 변하면 추적이 잘못 이어진 것으로 본다.
DEFAULT_MAX_AREA_RATIO = 3.0
# 프레임 간 중심 이동이 박스 대각선의 이 배율을 넘으면 같은 물체가 아니다.
DEFAULT_MAX_CENTER_SHIFT_RATIO = 1.5


@dataclass(frozen=True)
class Candidate:
    """확정된 사람 후보. `person-candidates.schema.json`의 항목 하나가 된다."""

    track_id: int
    confidence: float
    box_dict: dict[str, float]
    observations: int

    def as_dict(self) -> dict:
        return {
            'trackId': self.track_id,
            'confidence': round(self.confidence, 3),
            'box': self.box_dict,
            # 25.3의 지도 좌표 추정은 SLAM과 LiDAR 융합이 필요하다.
            # S15P11A301-137 이후에 채운다. 스키마가 null을 허용한다.
            'position': None,
        }


@dataclass
class _TrackHistory:
    """급변 검사를 위한 직전 상태."""

    area: float
    center: tuple[float, float]
    diagonal: float


class CandidateFilter:
    """track을 25.2 기준으로 걸러 후보로 만든다."""

    def __init__(
        self,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        min_observations: int = DEFAULT_MIN_OBSERVATIONS,
        max_area_ratio: float = DEFAULT_MAX_AREA_RATIO,
        max_center_shift_ratio: float = DEFAULT_MAX_CENTER_SHIFT_RATIO,
    ) -> None:
        if min_observations < 2:
            # 단일 프레임 확정을 설정으로도 만들 수 없게 한다. 25.2의 첫 문장이다.
            raise ValueError(
                'min_observations는 2 이상이어야 한다. '
                '25.2가 단일 프레임 확정을 금지한다'
            )
        self.window_seconds = window_seconds
        self.min_observations = min_observations
        self.max_area_ratio = max_area_ratio
        self.max_center_shift_ratio = max_center_shift_ratio
        self._history: dict[int, _TrackHistory] = {}
        # 급변으로 걸러낸 track. 로그에 사유를 남기기 위해 들고 있는다.
        self.last_rejections: list[str] = []

    def confirm(self, tracks: list[Track], now: float) -> list[Candidate]:
        """확정 조건을 만족한 후보만 돌려준다."""
        self.last_rejections = []
        confirmed: list[Candidate] = []
        alive = {track.track_id for track in tracks}

        for track in tracks:
            observations = track.observations_within(now, self.window_seconds)
            erratic = self._is_erratic(track)
            self._remember(track)

            if erratic:
                # 급변한 track은 확정하지 않는다. 다음 프레임에 안정되면 그때
                # 확정된다. track 자체를 지우지는 않는다 — 지우면 새 id가 발급돼
                # 1초 안정성이 처음부터 다시 시작한다.
                continue
            if observations < self.min_observations:
                continue
            confirmed.append(
                Candidate(
                    track_id=track.track_id,
                    confidence=track.confidence,
                    box_dict=track.box.as_dict(),
                    observations=observations,
                )
            )

        # 사라진 track의 이력을 정리한다. 남겨두면 id가 재사용될 때 옛 박스와
        # 비교해 잘못 급변으로 판정한다.
        for track_id in list(self._history):
            if track_id not in alive:
                del self._history[track_id]

        return confirmed

    def _remember(self, track: Track) -> None:
        box = track.box
        self._history[track.track_id] = _TrackHistory(
            area=box.area,
            center=box.center,
            diagonal=(box.width**2 + box.height**2) ** 0.5,
        )

    def _is_erratic(self, track: Track) -> bool:
        """직전 프레임과 비교해 비정상적으로 급변했는가 (25.2)."""
        previous = self._history.get(track.track_id)
        if previous is None:
            # 처음 본 track은 비교 대상이 없다. 급변이 아니다.
            return False

        box = track.box
        if previous.area > 0 and box.area > 0:
            ratio = max(box.area / previous.area, previous.area / box.area)
            if ratio > self.max_area_ratio:
                self.last_rejections.append(
                    f'track {track.track_id} 면적 {ratio:.1f}배 변화'
                )
                return True

        if previous.diagonal > 0:
            current_center = box.center
            shift = (
                (current_center[0] - previous.center[0]) ** 2
                + (current_center[1] - previous.center[1]) ** 2
            ) ** 0.5
            if shift / previous.diagonal > self.max_center_shift_ratio:
                self.last_rejections.append(
                    f'track {track.track_id} 중심 이동 '
                    f'{shift / previous.diagonal:.1f}× 대각선'
                )
                return True

        return False

    def reset(self) -> None:
        self._history.clear()
        self.last_rejections = []

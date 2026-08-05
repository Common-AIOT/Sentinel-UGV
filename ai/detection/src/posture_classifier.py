"""쓰러짐 판정 — 다중 신호 점수 기반 이진 분류.

학습 모델이 아니라 명시적인 규칙이다(AGENTS.md §10). 임계값은 설정에서 주입받으며
이 파일에 하드코딩하지 않는다.

## 이진 분류 (2026-08-03 변경)

출력은 NORMAL / FALLEN 두 값이다. 이전 3값 체계의 POSE_UNKNOWN이 관측의 약 21%를
차지해 다섯 중 하나는 답을 내지 못했다. 재난 탐색에서 관제가 필요한 답은
"쓰러졌나 아닌가" 하나다.

확신도는 라벨이 아니라 `fallen_score`(0~1)와 `signal_count`(쓴 신호 수)로 싣는다.

## 신호 4개 — 관절 없이도 판정한다

  1. torso_angle_deg       : 상체 기울기. 좌우(2D)와 앞뒤(단축률) 중 큰 값   [관절 필요]
  2. vertical_extent_ratio : 어깨-엉덩이 y 차이를 사람 크기로 정규화         [관절 필요]
  3. bbox_aspect_ratio     : bbox 가로/세로 비                              [관절 불필요]
  4. inactivity            : 정지 지속 시간 (MotionTracker). **배수로 적용**    [관절 불필요]

**관절이 안 잡혀도 3·4번이 남는다.** 문헌은 누운 자세가 원근 단축 왜곡과 가림 때문에
pose 추정이 가장 안 되는 구간이라고 지적한다. 즉 관절에만 의존하면 가장 필요한
순간에 가장 약한 신호를 쓰게 된다. 이전 구현은 관절이 부족하면 이미 계산해둔 bbox
신호마저 버리고 POSE_UNKNOWN을 냈다.

## 점수화

각 신호를 임계값 기준 시그모이드로 0~1에 매핑해 가중 평균한다. 이산 투표에서는
54도와 10도가 똑같이 "표 없음"이었지만, 점수는 이를 구분한다.

⚠️ 이 점수는 **보정된 확률이 아니다.** 가중치와 임계값은 실측 근거가 없는 값이며,
라벨 데이터 확보 후 로지스틱 회귀로 교체할 자리다(계획: 2단계).
"""

from __future__ import annotations

import math
from collections import Counter, deque

from .schemas import (
    POSTURE_NORMAL,
    POSTURE_FALLEN,
    Detection,
    PoseResult,
    PostureResult,
)


def _sigmoid_score(value: float, threshold: float, width: float, *, higher_is_fallen: bool) -> float:
    """값을 임계값 기준 0~1 점수로 편다.

    임계값에서 정확히 0.5가 되고, width만큼 떨어지면 약 0.73/0.27이 된다.
    width가 작을수록 예전의 이산 판정에 가까워진다.
    """
    if width <= 0:
        return 1.0 if (value >= threshold) == higher_is_fallen else 0.0
    z = (value - threshold) / width
    if not higher_is_fallen:
        z = -z
    # math.exp 오버플로 방지
    z = max(-60.0, min(60.0, z))
    return 1.0 / (1.0 + math.exp(-z))


class PostureClassifier:
    def __init__(
        self,
        *,
        torso_horizontal_deg: float = 55.0,
        bbox_aspect_ratio: float = 1.2,
        vertical_extent_ratio: float = 0.25,
        upright_angle_deg: float = 30.0,
        min_valid_keypoints: int = 4,
        keypoint_confidence: float = 0.5,
        depth_tilt: bool = True,
        torso_shoulder_ratio: float = 1.3,
        fallen_threshold: float = 0.5,
        weight_torso_angle: float = 1.0,
        weight_vertical_extent: float = 1.0,
        weight_bbox_aspect: float = 0.8,
        inactivity_boost: float = 0.4,
        width_torso_angle: float = 12.0,
        width_vertical_extent: float = 0.08,
        width_bbox_aspect: float = 0.25,
    ) -> None:
        self.torso_horizontal_deg = torso_horizontal_deg
        self.bbox_aspect_ratio = bbox_aspect_ratio
        self.vertical_extent_ratio = vertical_extent_ratio
        # 상체가 이 각도보다 수직에 가까우면 vertical_extent 신호를 **버린다**.
        #
        # 두 신호는 같은 것을 다르게 재는데, 어긋나면 한쪽이 틀린 것이다. 상체가
        # 수직인데 어깨-엉덩이 y 간격이 좁게 나오는 것은 물리적으로 불가능하므로,
        # 그때는 extent 쪽이 측정 오류다. 실측(2026-08-04, 관측 27,556건)에서
        # FALLEN 판정의 상체 각도 평균이 10.98°였다. 거의 수직인데 쓰러졌다고
        # 판정하고 있었다. 원인은 하체가 가려져 엉덩이 keypoint가 어깨 근처로
        # 잘못 찍히는 것이다(FALLEN일 때 엉덩이 검출률 23~28%, NORMAL은 41~45%).
        #
        # 임계값 조정이 아니라 **모순 제거**다. 같은 데이터에서 FALLEN 22.4% →
        # 13.1%로 줄었고, 71850의 실제 쓰러짐 트랙 점수는 전혀 변하지 않았다
        # (중앙 0.049, 최대 0.713 동일). 오탐만 제거된다.
        self.upright_angle_deg = upright_angle_deg
        self.min_valid_keypoints = min_valid_keypoints
        self.keypoint_confidence = keypoint_confidence
        # 카메라 광축 방향(앞뒤) 기울기를 각도에 반영할지. 아래 _depth_tilt_deg 참고.
        self.depth_tilt = depth_tilt
        # 똑바로 선 사람의 (어깨중점→엉덩이중점 길이) / (어깨 폭) 기대비.
        # ⚠️ 해부학 통념에 따른 값이며 실측 근거가 없다. 실영상 확보 후 조정한다.
        self.torso_shoulder_ratio = torso_shoulder_ratio

        # 가중 평균 점수가 이 값 이상이면 FALLEN.
        self.fallen_threshold = fallen_threshold
        # 자세·형상 신호의 가중치. 부동은 여기 없다(아래 배수로 적용).
        self.weight_torso_angle = weight_torso_angle
        self.weight_vertical_extent = weight_vertical_extent
        self.weight_bbox_aspect = weight_bbox_aspect
        # 부동은 가중치가 아니라 **배수 상한**이다. 부동이 1.0일 때 점수가
        # (1 + 이 값)배가 된다. 자세·형상 점수가 0이면 곱해도 0이다.
        self.inactivity_boost = inactivity_boost
        # 시그모이드 폭. 작을수록 예전 이산 판정에 가깝다.
        self.width_torso_angle = width_torso_angle
        self.width_vertical_extent = width_vertical_extent
        self.width_bbox_aspect = width_bbox_aspect

    @staticmethod
    def _midpoint(
        a: tuple[float, float] | None, b: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        """양쪽이 다 있으면 중점, 한쪽만 있으면 그 점을 쓴다."""
        if a and b:
            return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        return a or b

    def _depth_tilt_deg(
        self,
        pose: PoseResult,
        torso_len: float,
        signals: dict,
    ) -> float | None:
        """카메라 쪽(앞뒤)으로 기운 각도를 상체 단축률에서 추정한다.

        torso_angle_deg는 이미지 좌표의 dx/dy로 계산하므로 **좌우 기울기만** 잡는다.
        카메라 광축 방향으로 기울면 dx가 거의 0이라 각도가 0으로 나온다. 복도를 주행하는
        UGV에서는 복도 방향으로 누운 사람이 정확히 이 배치가 되므로 사각지대가 된다.

        앞뒤로 기울면 상체는 짧아 보이지만 **어깨 폭은 그대로다**(회전축이 어깨선과
        나란하기 때문). 그래서 어깨 폭을 자로 삼아 단축률을 재고 각도로 환산한다.

            cos(기울기) ≈ (관측 상체 길이 / 어깨 폭) / 기대비

        어깨가 한쪽만 잡히면 폭을 알 수 없으므로 None을 돌려 이 신호를 쓰지 않는다.
        옆으로 돌아선 사람은 어깨 폭이 좁아져 비가 커지고, 그때는 1.0으로 잘려 0도가
        된다. 즉 이 추정은 **과소 추정 방향으로만 틀리며 오탐을 만들지 않는다.**
        """
        conf = self.keypoint_confidence
        left = pose.get("left_shoulder", conf)
        right = pose.get("right_shoulder", conf)
        if left is None or right is None:
            return None

        shoulder_width = math.hypot(left[0] - right[0], left[1] - right[1])
        if shoulder_width < 1e-6 or self.torso_shoulder_ratio <= 0:
            return None

        observed = torso_len / shoulder_width
        signals["torso_shoulder_ratio"] = round(observed, 3)

        # 기대비보다 길면 기울지 않은 것이다(또는 몸을 돌린 것). 1.0으로 잘라 0도로 둔다.
        cos_tilt = min(1.0, max(0.0, observed / self.torso_shoulder_ratio))
        return math.degrees(math.acos(cos_tilt))

    def classify(
        self,
        detection: Detection,
        pose: PoseResult | None,
        inactivity: float | None = None,
    ) -> PostureResult:
        """쓰러짐 점수를 계산하고 이진 라벨을 붙인다.

        inactivity는 MotionTracker가 준 부동 점수(0~1)다. None이면 그 신호를
        빼고 계산한다. 관절이 없어도 bbox와 부동으로 판정하므로, 이 함수는
        어떤 경우에도 NORMAL 또는 FALLEN을 반환한다.
        """
        signals: dict[str, float | bool | None] = {}
        # (점수, 가중치) 쌍. 사용할 수 있는 신호만 담긴다.
        scored: list[tuple[float, float]] = []

        # --- 신호 3: bbox 가로세로비 (관절 불필요) ---
        height = detection.height
        aspect = detection.width / height if height > 0 else 0.0
        signals["bbox_aspect_ratio"] = round(aspect, 3)
        s_aspect = _sigmoid_score(
            aspect, self.bbox_aspect_ratio, self.width_bbox_aspect, higher_is_fallen=True
        )
        signals["score_bbox_aspect"] = round(s_aspect, 3)
        scored.append((s_aspect, self.weight_bbox_aspect))

        # --- 신호 4: 부동 (관절 불필요) ---
        # 독립 항이 아니라 **배수**로 적용한다(아래). 여기서는 값만 기록한다.
        if inactivity is not None:
            signals["inactivity"] = round(inactivity, 3)

        # --- 관절 기반 신호 2개 ---
        reason = ""
        torso = self._torso_signals(detection, pose, signals)
        if torso is None:
            reason = "관절 부족 — 형상·부동으로 판정"
        else:
            torso_angle, extent = torso
            s_angle = _sigmoid_score(
                torso_angle, self.torso_horizontal_deg, self.width_torso_angle,
                higher_is_fallen=True,
            )
            signals["score_torso_angle"] = round(s_angle, 3)
            scored.append((s_angle, self.weight_torso_angle))

            # 상체가 명백히 수직이면 extent는 믿지 않는다(self.upright_angle_deg 참고).
            # 각도와 수직신장비는 같은 것을 다르게 재므로, 어긋나면 하체 가림으로
            # 엉덩이 keypoint가 틀린 경우다. 각도 신호는 그대로 쓴다.
            if torso_angle < self.upright_angle_deg:
                signals["vertical_extent_dropped"] = True
                reason = "상체 수직 — 수직신장비 신호 제외"
            else:
                s_extent = _sigmoid_score(
                    extent, self.vertical_extent_ratio, self.width_vertical_extent,
                    higher_is_fallen=False,
                )
                signals["score_vertical_extent"] = round(s_extent, 3)
                scored.append((s_extent, self.weight_vertical_extent))

        total_weight = sum(w for _, w in scored)
        base = sum(v * w for v, w in scored) / total_weight if total_weight > 0 else 0.0
        signals["score_base"] = round(base, 3)

        # 부동은 자세·형상 점수에 **곱한다.** 독립 항으로 더하면 가중 평균의
        # 일정 비율을 무조건 채우게 되어, 형상이 애매할 때 부동이 대신 점수를
        # 밀어 올린다. 실측(2026-08-03)에서 부동이 1.0일 때 FALLEN이 되는 가로비가
        # 1.47에서 0.93으로 떨어졌다. 0.93은 거의 정사각형이라 상반신만 잡힌
        # 앉은 사람도 걸린다.
        #
        # 곱셈이면 자세·형상이 낮을 때 부동이 아무리 커도 결과가 낮게 유지된다.
        # 문헌의 "최종 몸 방향 + 바닥에 머문 시간" 조합이 이 형태다.
        multiplier = 1.0
        signal_count = len(scored)
        if inactivity is not None:
            multiplier = 1.0 + inactivity * self.inactivity_boost
            signals["inactivity_multiplier"] = round(multiplier, 3)
            signal_count += 1

        score = min(1.0, base * multiplier)

        status = POSTURE_FALLEN if score >= self.fallen_threshold else POSTURE_NORMAL
        detail = f"점수 {score:.2f} (기본 {base:.2f} × 부동 {multiplier:.2f})"
        reason = f"{detail} · {reason}" if reason else detail

        return PostureResult(
            status=status,
            fallen_score=score,
            signal_count=signal_count,
            signals=signals,
            reason=reason,
        )

    def _torso_signals(
        self,
        detection: Detection,
        pose: PoseResult | None,
        signals: dict,
    ) -> tuple[float, float] | None:
        """(상체 기울기 각도, 수직 신장비)를 계산한다. 못 구하면 None.

        None을 돌려도 판정을 포기하지 않는다. 호출부가 형상·부동 신호로 계속
        진행한다. 이전 구현은 여기서 POSE_UNKNOWN을 반환해 이미 계산해둔 bbox
        신호까지 버렸다.
        """
        if pose is None:
            signals["torso_reason"] = "pose 미실행"
            return None

        valid = pose.valid_count(self.keypoint_confidence)
        signals["valid_keypoints"] = valid
        if valid < self.min_valid_keypoints:
            signals["torso_reason"] = f"유효 keypoint 부족 ({valid} < {self.min_valid_keypoints})"
            return None

        conf = self.keypoint_confidence
        shoulder = self._midpoint(
            pose.get("left_shoulder", conf), pose.get("right_shoulder", conf)
        )
        hip = self._midpoint(pose.get("left_hip", conf), pose.get("right_hip", conf))
        if shoulder is None or hip is None:
            signals["torso_reason"] = "어깨 또는 엉덩이 keypoint 없음"
            return None

        dx = hip[0] - shoulder[0]
        dy = hip[1] - shoulder[1]
        torso_len = math.hypot(dx, dy)
        if torso_len < 1e-6:
            signals["torso_reason"] = "상체 길이가 0에 가까움"
            return None

        # 수직축(0, 1) 기준 각도. 0도=수직(서 있음), 90도=수평(누움).
        # 이미지 좌표 기준이라 좌우 기울기만 잡힌다.
        torso_angle = math.degrees(math.atan2(abs(dx), abs(dy)))
        signals["torso_angle_lateral_deg"] = round(torso_angle, 2)

        # 앞뒤(카메라 광축) 기울기를 상체 단축률로 추정해 합친다.
        if self.depth_tilt:
            depth_angle = self._depth_tilt_deg(pose, torso_len, signals)
            if depth_angle is not None:
                signals["torso_angle_depth_deg"] = round(depth_angle, 2)
                torso_angle = max(torso_angle, depth_angle)

        signals["torso_angle_deg"] = round(torso_angle, 2)

        # 어깨-엉덩이의 수직 거리를 사람 크기로 정규화. 누우면 작아진다.
        scale = max(detection.height, torso_len)
        extent = abs(dy) / scale if scale > 0 else 0.0
        signals["vertical_extent_ratio"] = round(extent, 3)
        return torso_angle, extent


class PostureSmoother:
    """자세 판정의 프레임 간 흔들림을 완충한다(hysteresis).

    누운 사람은 팔다리가 몸에 가려져 keypoint가 한두 프레임씩 부족해진다. 그때마다
    점수가 출렁여 라벨이 뒤집히면 fallen 누적이 끊겨 이벤트를 놓치거나 화면이 깜빡인다.

    규칙: 최근 window 프레임의 다수결로 자세를 정한다. trackId별로 이력을 관리하므로
    사람이 섞이지 않는다.

    이진 라벨로 바뀌면서 unknown 예외 처리는 사라졌다(2026-08-03). 이제 판정이
    NORMAL/FALLEN 둘뿐이라 "모름을 무시한다"는 규칙이 필요 없다.
    """

    def __init__(self, *, window: int = 5) -> None:
        self.window = max(1, window)
        self._history: dict[int, deque[str]] = {}

    def smooth(self, track_id: int | None, result: PostureResult) -> PostureResult:
        """원본 판정을 받아 완충된 판정을 반환한다.

        원본 상태는 signals["raw_status"]에 남겨 임계값 조정 시 근거로 쓴다.
        """
        if self.window == 1 or track_id is None:
            # 완충 비활성이거나 추적 ID가 없으면 그대로 통과시킨다.
            return result

        hist = self._history.setdefault(track_id, deque(maxlen=self.window))
        hist.append(result.status)

        status, _ = Counter(hist).most_common(1)[0]
        if status == result.status:
            return result

        signals = dict(result.signals)
        signals["raw_status"] = result.status
        signals["window"] = list(hist)
        # 점수와 신호 수는 이번 프레임의 관측 그대로 옮긴다. 완충은 라벨만 바꾸며,
        # 여기서 빠뜨리면 완충이 걸린 관측만 점수 0 / 신호 0으로 보고된다.
        return PostureResult(
            status=status,
            fallen_score=result.fallen_score,
            signal_count=result.signal_count,
            signals=signals,
            reason=f"{result.reason} → 완충 적용({result.status}→{status})",
        )

    def forget(self, track_ids: set[int]) -> None:
        """더 이상 추적하지 않는 트랙의 이력을 지운다."""
        for tid in [t for t in self._history if t not in track_ids]:
            del self._history[tid]

    def reset(self) -> None:
        self._history.clear()

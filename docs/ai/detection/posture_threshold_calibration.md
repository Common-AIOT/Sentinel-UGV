# 자세 판정 임계값 검증 (ISSUE-06)

`src/posture_classifier.py`의 임계값을 **정답 라벨과 대조해 검증한 결과**입니다.
그동안 "실측 근거 없는 임의값"으로 남아 있던 항목입니다. (Jira: S15P11A301-98)

> **결론** 자세·형상 신호 3개와 그 임계값 5개는 **의도대로 동작합니다.**
> 변경할 이유가 없습니다. 다만 **부동(inactivity) 신호 3개는 여전히 미검증**입니다.

---

## 1. 무엇을 어떻게 쟀나

E-FPDS는 사람 박스마다 **쓰러짐(1) / 비쓰러짐(-1)** 정답이 붙어 있습니다. 그 박스를
잘라 Pose를 돌리고 우리 규칙(`PostureClassifier.classify`)을 적용해 판정을 대조했습니다.

```bash
python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid --sweep
```

**학습이 아닙니다.** 규칙은 그대로 두고 임계값이 맞는지만 확인했습니다
(AGENTS.md §10 "학습 모델이 아니라 명시적인 규칙").

---

## 2. 결과 — 두 분포가 깨끗하게 갈립니다

E-FPDS train split 4곳(건물·복도 환경), 정답 **2,658건**.

| split | 정밀도 | 재현율 | F1 |
|---|---|---|---|
| split1 | 0.946 | 0.980 | **0.963** |
| split10 | **0.992** | 0.911 | 0.950 |
| split2 | 0.853 | 0.920 | 0.885 |
| split3 | 0.797 | 0.888 | 0.840 |

```
쓰러짐   1,653건  fallen_score 중앙 0.919
비쓰러짐 1,005건  fallen_score 중앙 0.032     ← 29배 차이
```

**`fallen_threshold: 0.5`가 두 분포의 한가운데입니다.** 임계값 스윕(0.05~0.95)에서도
0.5 근처를 벗어나 개선되는 지점이 없었습니다.

### 검증된 값

```yaml
torso_horizontal_deg: 55.0
bbox_aspect_ratio: 1.20
vertical_extent_ratio: 0.25
upright_angle_deg: 30.0
fallen_threshold: 0.5
weight_torso_angle / weight_vertical_extent / weight_bbox_aspect
```

---

## 3. ⚠️ 처음 측정은 틀렸습니다 — split 구성을 봐야 합니다

E-FPDS **valid** split으로 먼저 쟀을 때 정밀도가 **0.725**로 나왔습니다. 원인은
데이터 구성이었습니다.

| split | 비쓰러짐 |
|---|---|
| valid/split12 | **4개** |
| valid/split13 | **410개** ← 가정집 거실 |

**valid의 비쓰러짐 414개 중 410개가 가정집 한 곳입니다.** 즉 그 숫자는 사실상
**"소파에 누운 사람을 얼마나 걸러내나"**를 잰 것이었고, 우리 배치 환경(복도·건물)과
무관했습니다.

거짓 양성 표본을 뽑아 보니 **전부 소파에 누워 쉬는 사람**이었고, 상체 각도 74~87도,
가로세로비 2.9~5.5로 **우리 규칙 기준으로는 명백히 누운 형태**였습니다. 규칙이 틀린
것이 아니라 **정의가 다른 것**입니다.

| | 쓰러짐 정의 |
|---|---|
| E-FPDS | **바닥에** 쓰러진 사람. 소파·침대는 정상 |
| 우리 명세 25.6 | **"누워 있는 형태"면 FALLEN.** 직전 상태와 무관 |

→ **평가 데이터의 split 구성을 확인하지 않고 전체 평균만 보면 안 됩니다.**

---

## 4. 검증되지 않은 것 — 부동 신호

E-FPDS는 **정지 이미지**라 시간 축이 없습니다. 따라서 이번 측정은 4신호 중
**3개(상체 각도·수직신장비·bbox 형상)로만** 돌렸습니다.

```yaml
# 아래 3개는 여전히 실측 근거가 없다
posture:
  inactivity_boost: 0.4
motion:
  still_ratio: 0.06
  full_still_seconds: 3.0
```

**이 값들이 담당하는 판단이 중요합니다** — "소파든 침대든 미동이 없으면 쓰러진 것으로
본다". 재난 현장에서 가구 위에 의식 없이 누워 있는 사람은 구조 대상이므로, 3절의
거짓 양성 260건은 **실제 운영에서는 오탐이 아닐 수 있습니다.**

관련 실측이 하나 있긴 합니다. 앉은 사람 오탐(S15P11A301-98)에서 **부동 배수가
문턱을 넘기는 것**을 확인해 `upright_angle_deg` 게이트를 넣었습니다. 다만 그것은
오작동을 잡은 것이지 정상 작동을 확인한 것은 아닙니다.

→ **검증하려면 영상 데이터가 필요합니다.** 정지 이미지로는 불가능합니다.

---

## 5. 상태 정리

| 항목 | 상태 |
|---|---|
| 자세·형상 임계값 5개 + 가중치 3개 | ✅ **정답 2,658건 대조 완료** |
| 부동 신호 3개 | ❌ 미검증 (영상 필요) |

오탐·미탐이 보고되면 **부동 신호 쪽을 먼저 의심하는 것**이 합리적입니다.

---

## 6. 재현

```bash
# 현재 설정의 성능
python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid

# 임계값 후보 스윕
python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid --sweep

# 관절 기여도 확인 (Pose 없이 형상만)
python scripts/calibrate_posture.py --src <E-FPDS/raw> --split valid --no-pose
```

split 단위로 나눠 보는 것을 권합니다(3절 참고). `--split test`는 E-FPDS 배포 조건상
**최종 결과 보고에만** 사용합니다.

### 인용 의무

> Fallen People Detection Capabilities Using Assistive Robot.
> S. Maldonado-Bascón et al. *Electronics* 2019.

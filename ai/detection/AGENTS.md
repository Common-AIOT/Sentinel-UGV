# AGENTS.md — Sentinel UGV 객체탐지(Detection) 서브프로젝트

## 0. 프로젝트 명세와의 정합성 (2026-07-29 감사, 2026-07-30 갱신)

**규범은 저장소 루트의 `docs/`다.** 이 문서와 `docs/`가 충돌하면 `docs/`를 따른다
(`docs/05-통신-서버-영상.md:77` "본 장과 31장이 다르면 31장을 따른다").

### 정합 확인 완료

| 항목 | 명세 근거 | 상태 |
|---|---|---|
| `pose_status` 2값 (NORMAL/FALLEN) | 25.6 (2026-08-03 개정) | ✅ |
| encounter 트리거 = 사람 약 1초 안정 관측 | 25.1, 개요 156행 | ✅ |
| 조건부 Pose (3프레임 연속, 약 2FPS, 3초 중단) | 25.6 (78행) | ✅ |
| 동일 자세 약 1.5초 | 78·444·454행 | ✅ |
| 공통 JSON 봉투 / ENCOUNTER_CONFIRMED | 31-5, 31-6 | ✅ |
| FALLEN을 의료 판정에 쓰지 않음 | 25.6 | ✅ |

### ⚠️ 명세 이탈 3건 — 팀 확인 필요

**이탈 1. 추적기: ByteTrack → BoT-SORT**

명세는 ByteTrack을 지정한다(`docs/07-AI-탐지-음성.md` 25.1·25.4·25.6).
현재 구현은 BoT-SORT를 쓴다.

- 사유: UGV는 주행하며 촬영하므로 카메라 움직임 보정(GMC)이 필요한데 ByteTrack에는 없다.
- 영향: 출력은 동일하게 `trackId`이며 백엔드·DB가 보는 값은 바뀌지 않는다. 경미.
- 주의: 추적·그룹화는 명세 17.2에서 **AI B 담당**이다. 중복 구현 전 담당자와 확인한다.

**이탈 2. ReID 활성화 — 명세가 명시적으로 제외한 항목**

명세는 정밀 재식별을 **세 곳에서 제외**한다.
- `docs/01-프로젝트-개요.md:85` "피해자 정밀 재식별" (제외 목록)
- `docs/01-프로젝트-개요.md:95` "다수 사람의 정밀 재식별" (제외 목록)
- `docs/07-AI-탐지-음성.md` 25.4 "정밀 재식별은 범위에서 제외한다"

현재 `configs/tracker_sentinel.yaml`은 `with_reid: True` + `yolo26n-reid.onnx`를 쓴다.

- 사유: 시야를 벗어난 사람의 trackId 유지. IoU 기반으로는 원리적으로 불가능함을 실측 확인
  (카메라 팬 2200px에서 GMC 보정 후에도 예측 오차 564px, 사람 폭 240px).
- 대가: Jetson Orin Nano 8GB에서 SLAM·Nav2와 자원을 나눠 쓰는데 ReID 추론이 추가된다.
  명세가 제외한 이유가 이 자원 제약일 가능성이 높다.
- **되돌리는 법**(명세 준수 상태로 복귀): `with_reid: False`, `proximity_thresh: 0.5`.
  되돌리면 시야 이탈 후 ID 유지는 포기하고, 중복 판정은 명세 방식인
  지도 좌표 기반(1m/15초, 25.4)에 맡긴다.
- **2026-07-30 현재 로봇 프로파일(`configs/tracker_jetson.yaml`)은 이미 명세 준수 상태다**
  (`with_reid: False`). 이탈 상태로 남아 있는 것은 개발 PC 프로파일뿐이다(§7.1).
  단, 이 경우 시야 이탈 후 ID 유지가 안 되므로 팀 결정 자체는 여전히 필요하다.

**이탈 3. Pose를 전체 화면이 아니라 person crop으로 실행한다 (2026-07-30)**

명세 `docs/07-AI-탐지-음성.md` 25.6은 "YOLO26n Pose 조건부 실행 (약 2 FPS, **전체 화면 분석**)"이다.
현재 구현은 person bbox를 crop해서 사람별로 Pose를 돌린다.

- 사유: 전체 화면 Pose는 멀리 있는 작은 사람의 keypoint 품질이 떨어진다.
  person false negative를 최우선 리스크로 두는 원칙(§23)과 충돌한다.
- **"약 2 FPS"는 지킨다.** 2026-07-30에 `PoseScheduler`에 전역 예산을 넣어
  사람이 몇 명이든 파이프라인 전체 Pose 실행이 `max_fps`로 묶이도록 고쳤다.
  그 전에는 track별 예산만 있어 사람 N명일 때 초당 2N회였다(아래 참고).
- 영향: 출력 `pose_status` 값과 DB 스키마는 동일하다. 경미.
- **되돌리는 법**: `configs/*.yaml`의 `pose_trigger.global_budget: false`로 두면
  예전(사람 수 비례) 동작으로 돌아간다. A/B 비교용이며 기본값으로 쓰지 않는다.

> **고쳐진 성능 버그** — Jetson 실측(S15P11A301-150, 사람 4명)에서 63.5초 구간에
> 명세 기대 약 127회 대비 **Pose 300회**가 기록됐다. track별 예산이 사람 수만큼
> 곱해진 결과였다. 전역 예산 적용 후 개발 PC 검증에서 동일 영상 기준
> **Pose 30회 → 10회, detections는 571로 동일**(탐지력 손실 없음)을 확인했다.

**이 세 건은 AI 담당자 임의로 결정할 사안이 아니다.** 팀 논의로 명세를 개정하거나
구현을 되돌려야 하며, 결론이 날 때까지 이 절을 지우지 않는다.

### 📢 명세 개정 1건 — 팀 공지 필요 (2026-08-03)

**`pose_status`를 3값에서 2값으로 바꾸고 명세를 개정했다.**

```
이전: STANDING / POSSIBLE_FALLEN / POSE_UNKNOWN
이후: NORMAL / FALLEN   (+ fallenScore, signalCount)
```

- 개정 문서: `docs/07-AI-탐지-음성.md` 25.6·DB 주석, `docs/05-통신-서버-영상.md` 13장
- 사유: `POSE_UNKNOWN`이 관측의 약 21%를 차지해 다섯 중 하나는 답을 내지 못했다.
  재난 탐색에서 관제가 필요한 답은 "쓰러졌나 아닌가" 하나다.
- **깨지는 팀 계약은 없다**(사전 확인 완료):
  `common/schemas/*.json`에 `poseStatus` 필드가 없고, 백엔드 Java에 파싱 코드가 없으며,
  DB `detections.pose_status`는 `VARCHAR(32)`에 CHECK 제약이 없다. 마이그레이션 불필요.
- 그럼에도 **명세 개정이므로 팀에 알린다.** 관제 화면이 이 값을 표시하게 될 때
  2값 기준으로 만들어야 한다.

### 아직 구현하지 않은 항목 (이탈이 아님)

Detect 파인튜닝(78행), MQTT 발행(31-4), `mapPose`(SLAM 연동), Detect 상시 15FPS.

---

## 1. 문서 목적과 적용 범위

이 문서는 `ai/detection` 디렉터리(본 서브프로젝트) 내에서 작업하는 AI 코딩 에이전트(Codex 등)가
화요일~금요일 스프린트 동안 일관된 기준으로 구현을 수행하도록 만든 실무형 운영 문서다.

적용 범위는 `ai/detection` 하위 디렉터리로 한정한다. 이 저장소는 모노레포이며
`git rev-parse --show-toplevel` 기준 실제 Git 루트는 `ai/detection`의 상위 상위 디렉터리(`S15P11A301`)다.
따라서 이 문서의 모든 상대 경로는 별도 표기가 없는 한 **`ai/detection`을 기준**으로 한다.

이 문서는 단순 요약이 아니라 이후 모든 작업에서 참조해야 하는 규칙집이다. 지시가 실제 저장소 구조와
충돌하면 실제 구조를 우선하고, 불확실한 부분은 `확인 필요`로 명시한다.

---

## 2. 프로젝트 개요

### 프로젝트명
```text
Sentinel UGV
```

### 목적
재난·밀폐공간 탐색을 위한 AIoT 로봇 프로젝트. 주요 하드웨어와 소프트웨어 후보는 다음과 같다.

**하드웨어**
```text
RC car chassis
Jetson Orin Nano 8GB
Raspberry Pi
LiDAR
Camera
```

**소프트웨어 후보(전체 프로젝트 범위, 이번 스프린트 범위 아님)**
```text
ROS2
SLAM Toolbox
Nav2
YOLO
TensorRT
Dashboard
WebRTC
Faster-Whisper small
Piper TTS
Rule Engine
Optional small LLM
```

`ai/` 상위 디렉터리에는 이 `detection` 서브프로젝트 외에 음성 파이프라인 서브프로젝트인
[`ai/stt`](../stt/README.md)가 이미 존재한다. 두 서브프로젝트는 독립적으로 개발되며,
`detection`은 비전(YOLO) 트리거를, `stt`는 음성 기반 요구조자 파악을 담당한다.

---

## 3. 현재 개발 단계

```text
사전학습 모델 기반 end-to-end 추론 파이프라인 구현 완료 단계
(§28.1 연결 검증 게이트 통과. 남은 것은 데이터 학습과 로봇/백엔드 실연동)
```

**실행 순서가 2026-07-28에 변경되었다.** 기존 "데이터 → 학습 → 파이프라인" 순서에서
**"파이프라인 → 연결 검증 게이트 → 데이터 → 학습"** 으로 바꾸었다. 근거와 상세 계획은 §26 참고.

### 완료된 기반 작업
- Git 저장소 및 `ai/detection` 기본 디렉터리 구조(`data/`, `docs/`, `models/`, `notebooks/`, `runs/`, `src/`)
- `requirements.txt` 작성 (Python/CUDA/PyTorch 버전 고정)
- **miniforge conda 가상환경 `sentinel-yolo` 구축 완료 및 검증** — Python 3.10.20 / torch 2.11.0+cu128 /
  CUDA 12.8 사용 가능, ultralytics 8.4.104 (§7)
- **YOLO26 Detect/Pose 모델 지원 확인** (§13, §14)
- 객체탐지 MVP 범위 정의(`../../docs/ai/detection/requirements.md` 초안 존재 — 단, 클래스 범위는 §6 확인 필요 참고)
- Detect-first 파이프라인 설계 초안

### 완료된 구현 (2026-07-29 ~ 07-30)

| 항목 | 산출물 | 근거 절 |
|---|---|---|
| 추론 파이프라인 전체 연결 | `src/` 10개 모듈 | §10 |
| 명세 정렬(상태값 3종·1.5초·조건부 Pose) | `schemas.py`, `pose_estimator.py` | §0, §15 |
| encounter 트리거를 명세(사람 약 1초 안정 관측)로 교정 | `persistence.py` | §16 |
| 자세 흔들림 완충(PostureSmoother) | `posture_classifier.py` | §15 |
| ID 유지(BoT-SORT + GMC + ReID) | `configs/tracker_sentinel.yaml` | §0 이탈 2 |
| 명세 31-5 봉투 / 31-6 ENCOUNTER_CONFIRMED 페이로드 | `schemas.py` | §17 |
| 실행별 출력 디렉터리 분리 | `main.py` | §18 |
| 단위 테스트 31건 + 연결 검증 게이트 10건 | `tests/` | §22, §28.1 |
| Jetson Orin Nano 8GB + USB 카메라 프로파일 | `configs/pipeline.jetson.yaml`, `configs/tracker_jetson.yaml` | §7.1 |
| cwd 비의존 경로 해석(`_resolve_path`) | `pipeline.py` | §7.1 |
| 문서 위치를 루트 `docs/` 기준으로 정리 | `../../docs/ai/detection/`, `07-AI-탐지-음성.md` 25.7 | §9 |

**커밋**: `4d08b51`(구현), `629f43b`(문서 정리). 브랜치 `feat/ai/object-detection` → `develop` MR 생성 완료.

### 완료된 구현 (2026-07-30, 타 담당자 작업)

| 항목 | 산출물 | Jira |
|---|---|---|
| ROS2 토픽 구독 진입점 | `src/ros_main.py` | S15P11A301-153 |
| person_candidates 계약 변환 | `src/candidates.py`, `tests/test_candidates.py` | S15P11A301-133 |
| Jetson 실기기 검증 | Runbook 15장 | S15P11A301-150 |
| mission_manager 엔드투엔드 검증 | 상태 전이 확인 | S15P11A301-155 |

`person_detector_node`(임시 통합 구현)는 역할이 `ros_main.py`로 이관되고 제거되었다.
**메인 인식 로직은 항상 `ai/detection`이다.**

### 현재 진행 중 / 남은 것
- **AI-Hub 데이터셋 선정 및 확보 (ISSUE-03/04/05 차단 요인 — `data/raw`가 비어 있음, §11.1·§26)**
- ~~Detect 파인튜닝~~ → **2026-08-05 미채택 결정.** 데이터 3종 × 학습 방식 4종을 교차
  평가했고 사전학습을 이긴 조합이 없었다. 근거·재현 방법·평가 함정 3건은
  [`../../docs/ai/detection/finetuning_evaluation.md`](../../docs/ai/detection/finetuning_evaluation.md).
  **다시 시도하기 전에 그 문서의 4절(평가 방법의 함정)을 반드시 읽는다.**
- threshold 실측 조정 (ISSUE-06)
- **성능 미달**: Jetson 실측 9.45FPS로 목표 약 15FPS에 못 미친다.
  imgsz 축소 → TensorRT 변환 순서로 조정한다(§7.1, §35 11번).
- **MQTT 발행 미구현** — detection은 후보까지만 만들고, MQTT는 `cloud_bridge_node`가 담당한다(§17).
- `schemas.py`의 encounter 생성 코드가 Mission Manager 권한과 겹친다(§35 18번).

---

## 4. 최종 MVP 파이프라인

```text
입력 영상
→ YOLO person 탐지 + 추적(trackId 부여)
→ 사람을 약 1초 안정 관측  ← **이벤트 트리거**
→ (조건부) person crop → pretrained YOLO Pose 추론 → keypoint 추출
→ 규칙 기반 자세 판정
→ NORMAL / FALLEN (+ fallenScore, signalCount)  ← 트리거가 아니라 **속성**
→ ENCOUNTER_CONFIRMED 이벤트 발행 (명세 31-5 봉투)
→ JSONL 로그
→ 이벤트 이미지
→ 결과 시각화
```

**이벤트 트리거는 "사람을 찾은 것"이지 "쓰러진 것"이 아니다.** 프로젝트 명세를 따른다.

> "YOLO와 ByteTrack이 한 명 이상의 사람을 약 1초간 안정적으로 확인하면 encounter를 생성하고
> 탐사를 일시정지한다" (`docs/01-프로젝트-개요.md` 사용자 시나리오, `docs/04-자율주행.md` 8.1)

재난 현장에서는 서 있거나 앉아 있는 요구조자도 구조 대상이므로 자세로 거르지 않는다.
자세는 `pose_status` 속성으로 실어 보내고, 관제가 우선순위 판단에 참고한다.

이번 스프린트의 확정 범위는 **person 단일 클래스**다. 다중 클래스(장애물 등)는 다음 스프린트로
이월한다. 결정 근거와 이월 항목은 §6 참고.

**중요:** Pose 분기는 `person` 클래스에서만 트리거되므로, 나중에 Detect 클래스를 늘려도
이 파이프라인 구조는 바뀌지 않는다. 클래스 확장은 **재학습 + CLASS_MAP 변경**만으로 끝나야 한다.

---

## 5. 이번 스프린트 범위

- YOLO Detect로 person 탐지 + 추적(trackId)
- 사람 약 1초 안정 관측 시 encounter 이벤트 확정
- 조건부 Pose(3프레임 연속·약 2FPS) → keypoint 추출
- 규칙 기반 자세 판정(`NORMAL` / `FALLEN`)
- 명세 31-5 봉투로 JSONL 로그 및 이벤트 이미지 저장
- 결과 시각화(overlay)

이번 주에는 **Pose fine-tuning을 수행하지 않는다.** pretrained Pose 모델을 그대로 사용한다.

---

## 6. 제외 범위

다음은 이번 스프린트에서 명시적으로 제외한다.

```text
Pose fine-tuning
ROS2
Dashboard
INT8 quantization
LiDAR fusion
장애물 탐지 (다중 클래스)                    ← 다음 스프린트로 이월
Fire Extinguisher / Exit / Danger Sign 클래스 탐지  ← 다음 스프린트로 이월
```

### Tracking(ByteTrack) 채택 결정 (2026-07-29 변경)

**기존 계획에서 Tracking은 Drop 항목이었으나, 채택으로 변경했다.**

근거:
- 프로젝트 명세가 ByteTrack을 MVP 필수로 정의한다. `docs/01-프로젝트-개요.md` 156행
  "YOLO와 **ByteTrack**이 한 명 이상의 사람을 약 1초간 안정적으로 확인하면 encounter를 생성한다",
  MVP-06, 그리고 2주차(07-24~07-31) AI 목표에 ByteTrack이 포함되어 있다.
- 관제 보고 페이로드가 `trackId`를 요구한다(명세 31-6 `persons[].trackId`,
  DB `detections.track_id`, `encounter_victims.track_id`).
- **§16 persistence가 추적 없이는 성립하지 않는다.** 사람이 2명 이상이면 A가 0.5초,
  B가 0.5초 누웠을 때 "1초 연속"으로 오판한다. 지속 시간은 사람 단위로 재야 한다.
- 구현 비용이 낮다. Ultralytics에 ByteTrack이 내장되어 `model.track(persist=True)`로 해결된다.
  별도 학습이나 외부 저장소가 필요 없다.

**역할 분담 주의:** 명세 17.2에서 "사람 위치·추적·그룹화"는 **AI B** 담당이다.
현재 구현은 detection 내부의 persistence 정확도 확보를 위한 것이며,
사람 위치 추정(LiDAR 결합)·그룹화는 여전히 AI B 범위다. 중복 구현 전에 담당자와 확인한다.

multi-person은 ByteTrack 도입으로 자연히 지원된다(별도 작업 아님).

"이월" 표시 항목은 폐기가 아니라 **다음 스프린트 예정**이라는 뜻이다. 이번 주에 구현하지는 않되,
설계가 이를 막지 않도록 한다(§11 CLASS_MAP 확장성).

### 클래스 범위 결정 (2026-07-28 확정)

**결정: 이번 스프린트는 `person` 단일 클래스로 학습하고, 다중 클래스는 다음 스프린트에서 확장한다.**

근거:
- 이번 주의 핵심 산출물은 클래스 개수가 아니라 **Detect → Crop → Pose → Rule → 이벤트 저장이
  end-to-end로 동작하는 것**이다. 클래스를 늘려도 이 파이프라인 구조는 바뀌지 않는다.
- person + 장애물을 함께 라벨링해둔 AI-Hub 데이터셋이 사실상 없어, 다중 클래스는 직접 라벨링을
  수반한다. 1주 일정에서 가장 큰 리스크다.
- 따라서 **CLASS_MAP만 확장하면 되도록 설계**해두고(§11), 데이터가 준비되면 재학습만 한다.

기존 `../../docs/ai/detection/requirements.md`에 명시된 `Fire Extinguisher`, `Exit`, `Danger Sign` 3종과 `unknown` 상태는
**폐기가 아니라 다음 스프린트로 이월**한다. ISSUE-01에서 `../../docs/ai/detection/requirements.md`를 정합화할 때
"이번 스프린트 범위"와 "이월 항목"을 절로 나눠 두 문서의 불일치를 해소한다.

### 향후 확장: 장애물 탐지 (다음 스프린트, 이번 주 구현 안 함)

팀 결정에 따라 확장 대상은 **주행 중 회피해야 할 물리적 장애물**이다. 착수 전 아래 경계를 반드시 확인한다.

**⚠️ LiDAR / Nav2와의 역할 중복 주의**

이 프로젝트는 LiDAR + SLAM Toolbox + Nav2를 이미 포함한다. **주행 장애물 회피는 원칙적으로 Nav2의
costmap이 LiDAR로 처리하는 영역**이며, 같은 목적으로 YOLO를 추가하면 기능이 중복되고 Orin Nano 8GB의
자원을 추가로 소모한다(`ai/README.md`: 비전·SLAM·Nav2가 자원을 나눠 쓰므로 경량화가 전제).

따라서 카메라 기반 장애물 탐지는 **LiDAR가 구조적으로 놓치는 대상에 한정**하는 것이 타당하다.

| 대상 | 담당 | 이유 |
|---|---|---|
| 일반 벽·기둥·큰 잔해 | **LiDAR / Nav2** | 거리 측정이 정확하고 이미 구현 경로가 있음 |
| 유리·투명 아크릴 | 카메라 보완 검토 | LiDAR가 투과·반사로 놓침 |
| 낮은 턱, 바닥 케이블, 계단 하강 | 카메라 보완 검토 | 2D LiDAR 스캔 평면 아래라 미검출 |
| 출구·소화기·위험표지 | **카메라 / YOLO** | 의미 인식은 LiDAR로 불가 (관제 보고용) |

**다음 스프린트 착수 시 결정 필요:** 카메라 장애물 탐지가 Nav2 costmap에 입력으로 들어가는지
(= 실제 회피에 쓰임), 아니면 관제 화면 표시용인지. 전자면 좌표계 정합과 지연 요구사항이 훨씬 엄격해진다.

**참고 데이터셋 후보(미검증, 다음 스프린트에서 조사):** AI-Hub의 `배달로봇 비도로 자율주행 데이터`
(2D 이미지 + 3D LiDAR, 주행가능영역·동적 장애물 라벨), `장애물에 가려진 객체 인식 데이터`.
두 후보 모두 이번 스프린트에서는 검토하지 않는다.

---

## 7. 개발 환경

`requirements.txt`(저장소에 실제로 존재, 확인 완료)를 기준 환경으로 삼는다.

```text
Python 3.10.20
CUDA 12.8

torch==2.11.0+cu128
torchvision==0.26.0+cu128

ultralytics
opencv-python

numpy==1.26.4
pillow==12.3.0
PyYAML==6.0.3
requests==2.34.2
scipy==1.15.3
tqdm==4.69.0
```

PyTorch 인덱스:
```text
--extra-index-url https://download.pytorch.org/whl/cu128
```

### 실제 환경: miniforge conda 가상환경 (검증 완료)

이 프로젝트는 **Miniforge Prompt에서 conda 가상환경 `sentinel-yolo`를 활성화한 뒤** 작업한다.
2026-07-28 실측 검증 결과는 다음과 같으며, `requirements.txt`의 목표 버전과 **완전히 일치**한다.

| 항목 | 실측값 | requirements.txt 목표 | 일치 |
|---|---|---|---|
| conda 설치 경로 | `C:\Users\SSAFY\miniforge3` | — | — |
| 환경 이름 | `sentinel-yolo` | — | — |
| 인터프리터 | `C:\Users\SSAFY\miniforge3\envs\sentinel-yolo\python.exe` | — | — |
| Python | 3.10.20 | 3.10.20 | ✅ |
| torch | 2.11.0+cu128 | 2.11.0+cu128 | ✅ |
| torchvision | 0.26.0+cu128 | 0.26.0+cu128 | ✅ |
| `torch.cuda.is_available()` | `True` | — | ✅ |
| CUDA runtime | 12.8 | 12.8 | ✅ |
| ultralytics | 8.4.104 | (버전 미고정) | ✅ 현재 버전 유지 |
| opencv-python | 4.11.0 | (버전 미고정) | ✅ 현재 버전 유지 |

`sentinel-yolo` 외에도 `computer_vision`, `ai_env`, `llm` 등 11개 환경이 같은 miniforge에 존재한다.
**반드시 `sentinel-yolo`를 사용하고, 다른 환경에 패키지를 설치하거나 다른 환경에서 학습을 돌리지 않는다.**

#### 셸별 실행 방법

**권장: Miniforge Prompt (사용자가 기존에 사용하던 방식)**
```bat
conda activate sentinel-yolo
python --version
```

**AI 에이전트가 도구로 실행할 때 (권장):** 활성화 없이 인터프리터 절대경로를 직접 호출한다.
Git Bash / PowerShell에서는 `conda activate`가 초기화되어 있지 않아 실패할 수 있으므로 이 방식이 안전하다.

```bash
"C:/Users/SSAFY/miniforge3/envs/sentinel-yolo/python.exe" --version
"C:/Users/SSAFY/miniforge3/envs/sentinel-yolo/python.exe" scripts/inspect_raw_data.py --help
```

**주의:** 맨 셸에서 `python`을 그대로 호출하면 Windows Store 실행 별칭
(`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`)이 잡혀 "Python was not found" 오류가 난다.
이 오류는 Python 미설치를 뜻하지 않는다. 위 절대경로 또는 `conda activate`를 사용하면 해결된다.

#### 환경 재확인 명령
작업 시작 시 다음으로 환경을 확인하고, 결과를 §33 보고서의 환경 항목에 기록한다.

```bash
"C:/Users/SSAFY/miniforge3/envs/sentinel-yolo/python.exe" -c "import torch,torchvision,ultralytics,cv2;print('py',__import__('sys').version.split()[0]);print('torch',torch.__version__,'cuda',torch.cuda.is_available(),torch.version.cuda);print('tv',torchvision.__version__);print('ultralytics',ultralytics.__version__);print('cv2',cv2.__version__)"
```

### 환경 관련 규칙
- **`sentinel-yolo` 환경 밖에서 이 프로젝트의 코드를 실행하거나 패키지를 설치하지 않는다.**
- **`conda create` / `conda remove`로 환경을 새로 만들거나 삭제하지 않는다.** 기존 `sentinel-yolo`를 사용한다.
- 패키지 설치가 꼭 필요하면 `conda install`보다 해당 환경의 `pip`를 사용하되(현재 torch가 pip cu128 휠로
  설치되어 있어 conda 혼용 시 의존성이 깨질 수 있음), 설치 전 반드시 사용자 승인을 받는다.
- Python 버전을 임의로 변경하지 않는다.
- PyTorch, torchvision, CUDA 버전을 변경하지 않는다.
- `requirements.txt`를 임의로 덮어쓰지 않는다.
- `pip freeze > requirements.txt`를 실행하지 않는다.
- 새로운 라이브러리를 자동 설치하지 않는다.
- 새로운 패키지가 필요한 경우 기존 패키지로 해결 가능한지 먼저 검토한다.
- 설치가 꼭 필요하면 이유와 대안을 먼저 보고한다.
- TensorFlow, FastAPI, Gradio, STT, TTS 관련 패키지는 이 서브프로젝트(`ai/detection`)에 추가하지 않는다
  (음성 관련 패키지는 [`ai/stt`](../stt/requirements.txt)에서 별도 관리).
- `ultralytics`와 `opencv-python`은 현재 설치 버전을 우선하며, 정확한 버전이 고정되어 있지 않다고 해서
  임의로 최신 버전으로 업그레이드하지 않는다.

---

## 7.1 실행 프로파일 — 개발 PC / Jetson (2026-07-30)

설정 파일이 두 벌이다. **둘을 섞지 않는다.**

| 프로파일 | 설정 | 추적기 설정 | 용도 |
|---|---|---|---|
| 개발 PC | `configs/pipeline.yaml` | `configs/tracker_sentinel.yaml` | 노트북 웹캠 동작 확인용. ReID 켬 |
| Jetson | `configs/pipeline.jetson.yaml` | `configs/tracker_jetson.yaml` | 로봇 탑재용. USB 카메라, ReID 끔(명세 준수) |

```bash
python -m src.main --source 0 --config configs/pipeline.jetson.yaml --output runs/jetson
```

**Jetson 프로파일에서 개발 PC와 다른 점**
- 카메라: 1280x720 MJPG, backend `v4l2` (개발 PC는 `auto`)
- `imgsz: 640`, Pose `imgsz: 320`, `quantize: 16`
- `with_reid: False`, `proximity_thresh: 0.5` → **§0 이탈 2가 Jetson에서는 발생하지 않는다.**
  ReID를 켜려면 `yolo26n-reid.onnx` 배치 + `onnxruntime` 설치가 필요하고, SLAM·Nav2와
  자원을 나눠 쓰는 상황에서 여유가 있는지 실측 후 결정한다.

**규칙**
- 설정 파일 안의 상대 경로는 `_resolve_path()`가 `ai/detection`을 기준으로 해석한다.
  systemd·ROS2 launch로 띄우면 cwd가 프로젝트 루트가 아니므로 이 동작에 의존한다.
  **경로를 cwd 기준으로 되돌리지 않는다.**
- 실행은 반드시 `python -m src.main`이다. `python src/main.py`는 상대 임포트 때문에 실패한다.
- `--show`는 헤드리스 Jetson에서 쓰지 않는다(미검증).
- **모델 가중치는 Git에 없다**(`.gitignore`가 `*.pt`/`*.onnx`를 막는다). Jetson에서는
  `models/`에 직접 내려받아 배치한다. 절차는 `../../docs/07-AI-탐지-음성.md` 25.7.

### Jetson 실기기 검증 결과 (2026-07-30, S15P11A301-150)

전문은 `../../docs/07-AI-탐지-음성.md` 25.7 "실기기 검증 결과". 요약만 옮긴다.

| 항목 | 실측값 |
|---|---|
| 보드 / 전원 | Jetson Orin Nano 8GB, 15W |
| JetPack / L4T | 6.x / R36.4.7 |
| Python / torch | 3.10.12 / 2.8.0 (`cuda.is_available()` True) |
| ultralytics / opencv / lap | 8.4.107 / 4.11.0 / 0.5.13 |
| **처리량 (직접 오픈)** | **9.45 FPS** — 사람 4명 상시, 스트리밍 동시 구동, **Pose 예산 수정 전** |
| 처리량 (토픽 구독) | 11.15~11.31 FPS, 디코딩 실패 0 |
| 후보 발행 | 5.02Hz, 133 스키마 위반 0건 |

**2차 실측(2026-07-31)에서 TensorRT로 16.23 FPS까지 올렸다.** 상세는 아래 §7.3.
다만 이는 **AI 단독** 수치이며 통합 상태 재측정 전까지 목표 달성으로 보지 않는다.

**실기기에서 드러난 제약 3가지**
- 표준 JetPack에 `lap`이 없다 → `pip install lap` (aarch64 wheel 있음)
- 가용 RAM 700MB 이하에서 `CUBLAS_STATUS_ALLOC_FAILED`. Jetson은 GPU가 시스템 RAM을 공유한다
- 스트리밍 스택 구동 중에는 `usb_cam`이 `/dev/video0`을 점유해 `src.main`이 카메라를 못 연다
  → 이때는 `src.ros_main`(토픽 구독)을 쓴다. 이것이 최종 통합 구조다

**참고**
- `requirements-jetson.txt`는 만들지 않았다. JetPack의 torch는 NVIDIA 배포판을 써야 하므로
  pip 핀으로 고정할 대상이 아니다. 설치 절차는 04 25.7 "전제 조건"을 따른다.
- 전원 모드는 **15W가 Orin Nano 8GB의 상한**이다. 상향으로 얻을 여유가 없다.

### 7.2 성능 최적화 절차 (ISSUE-06)

**추측으로 설정을 바꾸지 않는다.** 반드시 계측 → A/B → 판정 순서를 따른다.

**계측**: `PipelineStats`가 단계별 소요 시간을 낸다(`stage_ms_per_frame`, `stage_share`,
`pose_ms_per_run`). 어떤 실행에서든 종료 시 출력되므로 병목을 바로 읽을 수 있다.

**A/B**: `scripts/bench_jetson.py`가 설정 조합을 순차 실행하고 표로 비교한다.

```bash
python scripts/bench_jetson.py --source <기준영상> \
    --config configs/pipeline.jetson.yaml --fp16 --warmup 30
```

- **반드시 고정 영상 파일로 돌린다.** 카메라는 매번 조건이 달라 A/B가 성립하지 않는다
  (스크립트가 카메라 입력을 거부한다)
- `--warmup`으로 모델 워밍업 프레임을 제외한 `steady_fps`를 본다. `avg_fps`로 비교하면 안 된다
- 주요 축: `--fp16`, `--models`(TensorRT 엔진 비교), `--imgsz`, `--pose-budget`

**판정 기준 — FPS만 보지 않는다.**

| 조건 | 판정 |
|---|---|
| `detections` 1% 이상 감소 | **기각.** FPS 이득과 상쇄되지 않는다(§23) |
| `detections` 유지 + FPS 상승 | 채택 후보 |

**런 간 편차는 약 ±3%다.** 그보다 작은 차이를 개선으로 보고하지 않는다.

## 7.3 Jetson 성능 실측 결론 (2026-07-31, S15P11A301-98)

전문은 `../../docs/07-AI-탐지-음성.md` 25.7. **여기 결론을 추측으로 뒤집지 않는다.**

| 축 | 결론 |
|---|---|
| **TensorRT `.engine`** | **채택.** 12.82 → **16.23 FPS**(+26.6%), detect 72.4 → 52.5ms, 탐지력 손실 없음 |
| FP16 (`quantize`) | **효과 없음.** `.pt` 경로에서 detect 커널에 영향을 주지 못한다(+0.7%, 잡음) |
| 전원 모드 | **조정 불가.** 15W가 Orin Nano 8GB 상한 |
| Pose 예산 | 이미 명세 하한. Jetson 비중이 7%뿐이라 여지 없음 |
| 해상도 축소 | 미착수. 위로 부족할 때만 |

**단계별 비중이 개발 PC와 다르다.**

| | Detect | Pose | post |
|---|---|---|---|
| 개발 PC | 81% | 18% | 0.4% |
| **Jetson** | **93%** | 7% | 0.5% |

Jetson은 Detect 편중이 더 심하다. **Pose 쪽을 손봐서 얻을 게 거의 없다.**

### TensorRT 취급 규칙

**엔진은 Jetson에서 직접 굽는다**(크로스 빌드 불가, 장치·드라이버 종속).
`.gitignore`의 `*.engine`이 막고 있으므로 **커밋하지 않는다.**

**`PYTHONPATH` 전제가 필수다.** TensorRT는 JetPack에 설치돼 있지만 `.venv`가
`include-system-site-packages = false`로 차단한다. **venv 설정을 바꾸지 않는다**
— numpy 등이 시스템 패키지와 섞인다. 경로만 빌린다.

```bash
PYTHONPATH=/usr/lib/python3.10/dist-packages \
  .venv/bin/yolo export model=models/yolo26n.pt format=engine half=True device=0
```

**설정 기본값은 `.pt`를 유지하고 엔진은 `--model` 인자로 넘긴다.**
`pipeline.jetson.yaml`에 `.engine`을 박으면 엔진을 굽지 않은 기기에서
clone 직후 실행이 깨진다. 배포 이미지가 고정되면 그때 기본값으로 올린다.

### 아직 목표 달성이 아니다

16.23 FPS는 **AI 단독** 수치다. 실제로는 스트리밍(x264enc, CPU 74%)·SLAM·녹화와
자원을 나눠 쓰며, S15P11A301-131에서 그 경합으로 오디오 38%를 잃은 전례가 있다.
**통합 상태 재측정 전까지 "15FPS 달성"으로 보고하지 않는다**(§22).

---

## 8. 저장소 분석 절차

작업을 시작하기 전 매번 다음을 순서대로 수행한다.

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short
conda activate sentinel-yolo && python --version   # 또는 §7의 절대경로 방식
```

이어서 `ai/detection` 구조를 최대 깊이 3 수준으로 분석하고, 아래 항목의 실제 존재 여부를 확인한다
(**2026-07-30 확인 결과** 병기).

| 경로 | 상태 |
|---|---|
| `src/` | 존재, 모듈 12개 구현 완료(§10) |
| `configs/` | 존재 — `pipeline.yaml`, `pipeline.jetson.yaml`, `tracker_sentinel.yaml`, `tracker_jetson.yaml`(§7.1) |
| `tests/` | 존재 — `test_posture_persistence.py`(31건), `test_candidates.py`. `__init__.py` 없음 |
| `models/` | 존재 — `yolo26n.pt`, `yolo26n-pose.pt`, `yolo26n-reid.onnx`. **Git 추적 안 됨**(§7.1) |
| `runs/` | 로컬 산출물. `.gitignore`에 `ai/detection/runs/` 등록됨 |
| `scripts/` | **미존재** — 필요 시 새로 생성 |
| `docs/` | 존재(`.gitkeep.txt`만 유지). detection 문서 원본은 루트 `../../docs/ai/detection/`로 이동 |
| `data/` | 존재 (`raw/`, `processed/`, `pose_test/`만 존재, 각 `.gitkeep.txt`). **`raw/`는 여전히 비어 있음** |
| `notebooks/` | 존재(내용 없음) — 용도 확인 필요 |
| `README.md`(detection 전용) | **미존재** (상위 `ai/README.md`, `../../docs/ai/README.md`만 존재) |
| `requirements.txt` | 존재. **`lap`·`onnxruntime` 미반영**(§35 9번) |
| `requirements-jetson.txt` | **미존재 — 의도적**(JetPack 버전 미확인, §7.1) |
| `pyproject.toml` / `setup.cfg` | **미존재** |
| `.gitignore`(detection 전용) | **미존재** (Git 루트 `.gitignore`가 적용됨) |
| `AGENTS.md` | 이 문서 |
| `CLAUDE.md` | **미존재** |

**문서 위치 규칙(2026-07-29 변경).** detection 문서의 원본은 저장소 루트 `docs/` 아래에 둔다.

| 문서 | 위치 |
|---|---|
| Jetson 실행·검증·성능·오류 대처 | `../../docs/07-AI-탐지-음성.md` 25.7 [구현 기준] — **단일 출처** |
| 객체탐지 요구사항 초안 | `../../docs/ai/detection/requirements.md` |
| 에이전트 운영 규칙 | 이 문서 (`ai/detection/AGENTS.md`) |

`ai/detection/docs/`에 새 문서를 만들지 않는다. 코드 옆에 두면 루트 `docs/`와 이중화된다.

**2026-07-30: `docs/ai/detection/jetson_runbook.md`를 삭제하고 내용을 04 25.7로 통합했다.**
같은 절차가 두 문서에 있어 한쪽만 갱신되는 문제가 실제로 발생했기 때문이다
(런북 12장은 갱신됐는데 04의 성능 조정 순서는 옛 내용으로 남아 있었다).
**Jetson 관련 내용은 04 25.7에만 쓴다.**

기존 `AGENTS.md`는 없었으므로 병합 없이 신규 생성했다. 이후 이 문서를 수정할 때는 삭제 후 재작성하지 말고
기존 유효 규칙을 확인한 뒤 병합한다.

---

## 9. 디렉터리 정책

```text
ai/detection/
├── data/
│   ├── raw/         # 원본 데이터 (read-only 취급, 존재)
│   ├── interim/      # 중간 산출물 (미존재, 필요 시 생성)
│   ├── processed/    # 최종 YOLO 데이터 (존재)
│   ├── samples/       # 시각화 샘플 (미존재, 필요 시 생성)
│   └── pose_test/     # Pose 테스트 영상 (존재)
├── docs/               # 로컬 placeholder만 유지. 문서는 루트 ../../docs/ai/detection/ 기준
├── models/            # 모델 가중치 (Git 추가 금지, .gitignore가 *.pt/*.onnx 차단)
├── notebooks/          # 탐색용 노트북(용도 확인 필요)
├── runs/               # 추론 산출물 (Git 추가 금지, 실행별 타임스탬프 하위 폴더)
├── scripts/            # CLI 스크립트 (미존재, 필요 시 생성)
├── configs/            # 실행 프로파일 4종 (§7.1)
├── src/                # 파이프라인 모듈 10개 (§10)
├── tests/              # test_posture_persistence.py (31건)
├── requirements.txt
└── AGENTS.md
```

원본 데이터와 모델 가중치는 Git에 추가하지 않는다. 저장소 루트 `.gitignore`가 적용되므로, 새 산출물
디렉터리를 만들 때 최상위 `.gitignore` 정책과 충돌하지 않는지 확인한다.

---

## 10. 모듈 아키텍처

**아래 모듈은 2026-07-29 기준 모두 구현 완료 상태다.** 새로 만들지 말고 기존 파일을 확장한다.

```text
src/schemas.py            # 데이터 구조 + 명세 31-5 봉투 생성
src/object_detector.py    # Detect + ByteTrack
src/pose_estimator.py     # crop → Pose → 원본 좌표 복원
src/posture_classifier.py # 규칙 기반 자세 판정
src/persistence.py        # trackId별 지속 시간 관리
src/pipeline.py           # 전체 연결
src/logger.py             # JSONL
src/storage.py            # 이벤트 이미지
src/visualize.py          # overlay
src/main.py               # CLI entry point (카메라 직접 오픈, 단독 검증용)
src/ros_main.py           # ROS2 진입점 (토픽 구독, 로봇 통합용) — 2026-07-30 추가
src/candidates.py         # person_candidates 계약 변환 — 2026-07-30 추가
```

**진입점이 두 개다.** 둘 다 같은 `InferencePipeline`을 호출하며, 추론 로직은 하나뿐이다.

| 진입점 | 입력 | 용도 |
|---|---|---|
| `src.main` | `cv2.VideoCapture`로 카메라·영상 직접 오픈 | 단독 검증 |
| `src.ros_main` | `/camera/image_raw/compressed` 구독 | **로봇 통합(최종 구조)** |

로봇에서는 `usb_cam` 노드가 `/dev/video0`을 단독 점유하므로(명세 9.6 카메라 단일 오픈 원칙)
`src.main`으로 카메라를 열 수 없다. 실기기에서 실제로 재현되었다(runbook 13장).

임계값은 `configs/pipeline.yaml`(개발 PC) / `configs/pipeline.jetson.yaml`(로봇)에서 관리하며
코드에 하드코딩하지 않는다(§7.1).

### object_detector.py
- Ultralytics Detect 모델 로딩 — **모델 경로를 인자로 받는다(하드코딩 금지).**
  사전학습 `yolo26n.pt`와 파인튜닝 가중치를 인자만 바꿔 교체할 수 있어야 한다(§26 실행 순서 변경).
- 이미지/프레임 추론
- person 필터링 — **class id가 아니라 class name으로 판정**(§11 확장성)
- confidence threshold
- bbox 반환
- 모델 내부 구현을 직접 수정하지 않음

### pose_estimator.py
- pretrained Pose 모델 로딩
- person crop 입력
- keypoint 추론
- 원본 프레임 좌표계로 복원할 수 있는 메타데이터 제공

### posture_classifier.py
- keypoint 기반 rule classifier + `PostureSmoother`(흔들림 완충)
- 상태: `NORMAL` / `FALLEN` (명세 25.6, §15). 점수 기반 이진 판정
- 학습 모델이 아니라 명시적인 규칙 기반 구현

### pose_estimator.py 의 PoseScheduler
- 조건부 Pose 실행 판단(명세 25.6): 3프레임 연속 감지 → 활성, 약 2FPS, 3초 미감지 → 중단
- 실행하지 않는 프레임에서는 직전 판정을 재사용해 자세 깜빡임을 막는다
- 매 프레임 Pose를 돌리면 Detect FPS를 유지할 수 없다(실측: 적용 후 5.8 → 11.5 FPS)
- **예산이 두 겹이다(2026-07-30).** track별 간격 + **전역 간격**.
  명세의 "약 2FPS"는 파이프라인 전체 예산이므로(§0 이탈 3), 사람이 몇 명이든
  총 실행 횟수가 `max_fps`로 묶인다. 자격을 갖춘 track들에 **라운드로빈**으로 배분해
  특정 한 명만 갱신되고 나머지가 굶는 일을 막는다
- **파이프라인은 `select(detections, t)`를 쓴다.** 프레임 전체를 봐야 전역 예산을
  나눌 수 있기 때문이다. `should_run(det, t)`는 단일 탐지용으로 남겨두며
  전역 예산을 선착순으로 적용한다

### persistence.py
- `PersistenceTracker` — trackId별 연속 관측 시간과 `FALLEN` 지속 시간 누적
- encounter 확정(사람 1.5초 안정 관측)과 쿨다운 판정
- `memory.forget_seconds` 안이면 ID가 바뀌어도 시간·위치로 상태를 승계한다(§16)

### pipeline.py
- Detect → Filter → Crop → Pose → Rule → Smoother → Persistence → Log → Save 연결
- 각 모듈을 호출하되 모델 구현을 중복 포함하지 않음
- `_sync_track_buffer()` — 실측 FPS로 매 프레임 `track_buffer`를 재계산해
  기억 시간을 프레임이 아닌 **초** 기준으로 고정한다(§16)
- `_resolve_path()` — 설정의 상대 경로를 `ai/detection` 기준으로 해석한다(§7.1)
- `_open_camera()` — backend 선택(`auto`/`v4l2`/`gstreamer` 등). Jetson USB 카메라 대응

### visualize.py
- overlay 렌더링. **원본 프레임을 수정하지 않고 항상 복사본에 그린다.**

### ros_main.py (2026-07-30 추가, S15P11A301-153)
- `/camera/image_raw/compressed` 구독 → `imdecode` → `InferencePipeline.process_frame()`
- QoS: `BEST_EFFORT` / `KEEP_LAST` / `depth=1` — 추론이 프레임보다 느리면 항상 최신 프레임만 처리한다
- 확정 후보를 `/perception/person_candidates`로 5Hz 발행
- **encounter를 발행하지 않는다.** encounter 발급 권한은 Mission Manager 단독이다(명세 26.1)
- 추론이 멈추면 오래된 후보를 재발행하지 않고 빈 배열로 바꾼다(`stale_seconds`)

### candidates.py (2026-07-30 추가)
- `PersonObservation` → `common/schemas/person-candidates.schema.json` 계약 변환
- `trackId`가 없는 탐지는 후보에서 제외한다(스키마가 int ≥ 0을 요구)
- 후보가 없어도 **빈 배열을 계속 발행**한다. mission_manager가 "사람 없음"과 "노드 죽음"을 구별해야 한다
- `position`(지도 좌표)은 `human_localizer` 연동 전까지 `None`

### logger.py
- JSONL 이벤트/프레임 로그 작성
- UTF-8
- 한 줄당 하나의 JSON 객체
- flush 및 파일 예외 처리

### storage.py
- 이벤트 이미지 저장
- 출력 디렉터리 생성
- 파일명 충돌 방지
- 원본 프레임 수정 금지

### schemas.py
- Detection, Pose, Posture, Event 데이터 구조 정의
- dataclass 또는 TypedDict 사용 가능
- 과도한 schema framework 도입 금지

### main.py
- CLI entry point
- 입력 영상, 모델, threshold, 출력 경로 설정
- 파이프라인 실행

### 저장소 분석 및 작업 규칙(공통)

**작업 시작 순서**
```text
현재 경로 확인 → Git 루트 확인 → 브랜치 확인 → 변경사항 확인
→ 프로젝트 구조 분석 → 기존 코드 분석 → 기존 테스트 분석 → 실제 데이터 구조 분석
→ 구현 계획 작성 → 구현 → 검증 → 결과 보고
```

**기존 코드 우선**
- 기존 코드가 있으면 재사용한다.
- 같은 기능의 파일을 중복 생성하지 않는다.
- 유사한 역할의 파일이 있으면 기존 파일을 확장한다.
- 기존 API를 불필요하게 변경하지 않는다.
- 현재 작업과 무관한 파일을 수정하지 않는다.
- 저장소 전체를 포매팅하지 않는다.
- 동작 중인 코드를 이유 없이 리팩터링하지 않는다.

**구현 우선순위**
```text
동작하는 코드 > 읽기 쉬운 코드 > 추상화
```

MVP에서는 과도한 추상화를 금지한다. 다음을 피한다.
```text
Factory Pattern
Plugin Framework
Generic Pipeline Framework
불필요한 Interface 계층
불필요한 Dependency Injection
과도한 클래스 상속
```

---

## 11. 데이터셋 관리 규칙

- 원본 데이터는 수정하지 않는다. `data/raw`는 read-only로 취급한다.
- 중간 산출물은 `data/interim`(현재 미존재, 필요 시 생성).
- 최종 YOLO 데이터는 `data/processed`(존재).
- 시각화 샘플은 `data/samples`(현재 미존재, 필요 시 생성).
- Pose 테스트 영상은 `data/pose_test`(존재).
- 원본 데이터와 모델 가중치는 Git에 추가하지 않는다.
- 라벨 변환은 재현 가능해야 한다. seed를 고정한다(기본 42).
- 클래스 이름과 class id는 중앙(`configs/dataset.yaml` 및 `CLASS_MAP`)에서 관리한다.
- 빈 라벨 이미지의 처리 정책을 명확히 문서화한다.
- bbox를 이미지 범위로 clip한다.
- 아주 작은 bbox 제외 기준을 문서화한다.
- 중복 이미지 및 데이터 누수 가능성을 검사한다.
- 데이터셋 출처와 라이선스를 기록한다.

초기 클래스 맵(이번 스프린트 확정, §6):
```python
CLASS_MAP = {
    "person": 0,
}
```

### 클래스 확장성 요구사항 (필수)

다음 스프린트에 장애물 등 클래스가 추가되는 것이 **확정**되어 있으므로(§6), 이번 주 구현은
아래를 반드시 지켜 재작업이 발생하지 않게 한다. 지금 지키면 비용이 0이고, 나중에 고치면 전면 수정이다.

- **class id를 코드에 하드코딩하지 않는다.** `if cls == 0:` 금지. `CLASS_MAP` 또는
  `configs/dataset.yaml`의 `names`를 단일 출처로 삼아 조회한다.
- **person 필터링은 "class id가 0인가"가 아니라 "class name이 person인가"로 판정한다.**
  클래스가 늘면 id는 재배치될 수 있지만 이름은 안정적이다.
- `configs/dataset.yaml`의 `names`와 `CLASS_MAP`이 **어긋날 수 없는 구조**로 만든다
  (한쪽에서 읽어 다른 쪽을 생성하거나, 로드 시 일치를 검증).
- 변환/검증 스크립트는 **클래스 개수를 상수로 가정하지 않는다.** `nc`는 설정에서 읽는다.
- 단, **지금 다중 클래스 처리 로직을 미리 구현하지는 않는다.** 확장 가능한 구조까지만 만들고
  실제 다중 클래스 분기는 데이터가 생겼을 때 추가한다(§10 과도한 추상화 금지).

**Pose 트리거는 person에만 적용된다**(§4). 클래스가 늘어도 Pose 분기 조건은 바뀌지 않아야 한다.

---

## 11.1 AI-Hub 데이터 확보 절차

이번 스프린트의 학습 데이터는 **AI-Hub(<https://aihub.or.kr>)** 에서 확보한다.
`data/raw`는 현재 비어 있으므로(§8) ISSUE-03 이전에 반드시 이 절차를 완료해야 한다.

### 확보 절차

```text
1. AI-Hub 회원가입
2. 휴대폰 본인인증 완료          ← 내국인만 가능
3. 데이터셋 페이지 → [다운로드] 버튼 → 이용 목적 기재 후 신청
4. 자동승인 (인증 완료 이용자 대상, 승인 절차 간소화됨)
5. 다운로드
   5-a. 웹 브라우저 직접 다운로드, 또는
   5-b. aihubshell (AI-Hub 오픈 API 다운로더) + API key
6. 압축 해제 → data/raw/<dataset_name>/ 에 배치
7. scripts/inspect_raw_data.py 로 구조 확인 (ISSUE-03)
```

### 반드시 지킬 제약

- **내국인 본인인증 필수.** 인증이 불가능한 경우 신청서를 작성해 `aihub@aihub.kr`로 제출해야 하며,
  이 경우 승인까지 시간이 걸려 스프린트 일정에 영향을 준다.
- **디스크 여유 공간은 다운로드 용량의 2~3배 이상 확보한다.** (AI-Hub 공식 안내)
  AI-Hub 데이터셋은 수백 GB 규모가 흔하므로, **전체를 받지 말고 필요한 일부 파티션만 받는 것을 우선 검토한다.**
- **`data/raw`에 받은 원본은 절대 수정하지 않는다**(§11). 압축 해제 후 read-only로 취급한다.
- **원본 데이터를 Git에 커밋하지 않는다.** `data/raw` 하위가 `.gitignore`로 실제 제외되는지
  `git status --short` 및 `git check-ignore -v <path>`로 확인한다. 제외되지 않으면 사용자에게 보고한다.
- **AI Agent는 AI-Hub 계정 로그인, 본인인증, 신청, API key 발급을 대신 수행하지 않는다.**
  이 단계는 사용자가 직접 진행하고, 에이전트는 다운로드 완료된 `data/raw` 구조를 분석하는 것부터 담당한다.
- **API key를 코드나 문서에 하드코딩하지 않는다.** 환경변수 또는 Git에 포함되지 않는 로컬 설정으로 관리한다.
- 데이터셋별 **이용 조건과 출처를 `../../docs/ai/detection/dataset_selection.md`에 반드시 기록한다**(§26 ISSUE-02).

### 안심존(安心존) 데이터 주의

일부 데이터셋(특히 보건의료 데이터)은 일반 다운로드가 불가능하고 **온·오프라인 안심존을 통해서만 개방**되며
**IRB 심의 결과 통지서** 등 추가 서류가 필요하다. 안심존 데이터는 로컬에 내려받아 학습할 수 없으므로
**이번 캡스톤 스프린트에서는 사용 불가로 간주한다.** 후보 검토 시 이 항목을 최우선으로 확인한다.

### aihubshell 참고

AI-Hub는 다양한 개발환경용 데이터 다운로더 `aihubshell`을 제공하며, API key를 입력해 사용한다.
download 모드에서 `datasetkey`를 지정하면 명령을 실행한 위치에 다운로드가 진행된다.
자세한 사용법은 [AI 허브 오픈 API 이용안내](https://www.aihub.or.kr/devsport/apishell/list.do) 참고.

**확인 필요:** `aihubshell`은 Linux 환경 안내가 중심이다. 현재 개발 PC가 Windows이므로
Windows에서의 동작 여부(WSL 필요 여부 포함)는 실제 시도 전까지 미확인이다. 웹 브라우저 직접 다운로드가
확실한 대안이다.

---

## 12. YOLO 데이터 변환 규칙

`scripts/`는 아직 없으므로 아래 스크립트는 ISSUE-04에서 신규 생성한다.

```text
scripts/convert_to_yolo.py
scripts/split_dataset.py
scripts/validate_yolo_dataset.py
scripts/visualize_labels.py
configs/dataset.yaml
```

### convert_to_yolo.py
- 실제 JSON 구조를 먼저 확인한다(추정으로 parser를 작성하지 않는다).
- person만 변환한다.
- bbox clipping, invalid bbox 제거
- YOLO 좌표 정규화
- 변환 통계 및 JSON 보고서 출력

### split_dataset.py
- 기본 비율 0.8 / 0.1 / 0.1, seed 42
- 이미지/라벨 pair 유지
- 가능하면 group/scene 단위 split
- group 정보가 없으면 데이터 누수 위험을 문서화

### validate_yolo_dataset.py
- 라벨 값 5개(class_id, x, y, w, h) 검증
- class id 정수 및 범위 확인
- 좌표 0~1 범위 확인
- width/height 양수 확인
- 이미지/라벨 pair 확인
- 오류 시 exit code 1, 정상 시 exit code 0

### visualize_labels.py
- YOLO bbox를 픽셀 좌표로 복원, 랜덤 샘플(seed 고정)
- 결과는 `data/samples`에 별도 저장, 원본 수정 금지

### configs/dataset.yaml
```yaml
path: ../data/processed
train: images/train
val: images/val
test: images/test

names:
  0: person
```
실제 상대경로가 스크립트 실행 위치 기준으로 올바른지 반드시 확인한다.

---

## 13. 객체탐지 모델 개발 규칙

`scripts/train_detect.py`(신규 생성, ISSUE-05)에서 Ultralytics API만 사용한다.

```python
from ultralytics import YOLO
```

기본 설정:
```text
imgsz=640
optimizer="auto"
seed=42
plots=True
```
- smoke test: `epochs=1`
- baseline: `epochs=20`

**모델 지원 확인 완료(2026-07-28):** 설치된 `ultralytics 8.4.104`의 모델 설정 디렉터리에
`yolo26.yaml`이 존재하므로 **YOLO26 Detect 계열은 지원된다.** 계획대로 `yolo26n.pt`를 사용한다.

**확인 필요:** 설정(`.yaml`) 지원은 확인했으나 **사전학습 가중치 `yolo26n.pt` 파일은 아직 내려받지 않았다.**
최초 실행 시 Ultralytics가 자동 다운로드를 시도하므로, 네트워크 차단 환경이면 실패할 수 있다.
가중치는 `models/`에 배치하고 Git에 커밋하지 않는다.

만약 가중치 해석이나 로딩이 실패하면 **임의로 다른 모델로 바꾸고 성공했다고 보고하지 않는다.**
차단 요인으로 명시적으로 보고하고, 대체안(`yolo11n.pt` 등도 설치본에 존재)을 제안만 한 뒤 사용자 결정을 기다린다.

평가/조정(ISSUE-06):
- Detect confidence threshold 조정
- NMS IoU threshold 검토
- 최소 bbox 크기 조정
- FPS 측정, person false negative 최소화 우선
- 시간이 남으면 TensorRT FP16(INT8은 제외)

---

## 14. Pose 모델 개발 규칙

`scripts/test_pose.py`(신규 생성, ISSUE-05)에서 pretrained Pose 모델로
**inference만 수행하고 학습은 금지**한다.

**모델 지원 확인 완료(2026-07-28):** `ultralytics 8.4.104`에 `yolo26-pose.yaml`이 존재하므로
**YOLO26 Pose 계열도 지원된다.** 계획대로 `yolo26n-pose.pt`를 사용한다.
(가중치 파일 자체는 미다운로드 — §13의 확인 필요 항목과 동일.)

기록 항목:
- source 이미지/영상, confidence, device
- 결과 저장 경로
- 전체 프레임 수, keypoint 검출 프레임 수, keypoint shape
- 가능하면 FPS

검증 대상 영상: standing / sitting / bending / lying (§26의 Pose 테스트 영상 조건 참고).

이번 스프린트에서는 Pose fine-tuning을 수행하지 않는다(§5, §6).

---

## 15. 자세 판정 규칙

상태값은 프로젝트 명세 25.6을 그대로 쓴다. **2026-08-03에 3값에서 2값으로 개정했다.**

```text
NORMAL   쓰러짐 점수가 임계값 미만. 서 있든 앉아 있든 포함한다
FALLEN   쓰러짐 점수가 임계값 이상. 누워 있는 형태를 뜻하며 직전 자세와 무관하다
```

근거: `docs/07-AI-탐지-음성.md` 25.6, DB 스키마 (`pose_status VARCHAR NULL -- NORMAL | FALLEN`)

**`FALLEN`은 encounter 우선순위 상향과 관제 강조 표시에만 쓰고 의료적 판정으로
사용하지 않는다**(명세 25.6). 로봇은 진단하지 않는다.

### 신호 4개 — 관절이 없어도 판정한다

| 신호 | 내용 | 관절 필요 |
|---|---|---|
| `torso_angle_deg` | 좌우(2D) / 앞뒤(어깨 폭 대비 단축률) 중 큰 값 | ✅ |
| `vertical_extent_ratio` | 어깨-엉덩이 y 차이를 사람 크기로 정규화 | ✅ |
| `bbox_aspect_ratio` | bbox 가로/세로 비 | ❌ |
| `inactivity` | 정지 지속 시간 (`motion.py`) | ❌ |

문헌은 **누운 자세가 원근 단축 왜곡과 가림 때문에 pose 추정이 가장 어려운 구간**이라고
지적한다. 관절에만 의존하면 가장 필요한 순간에 가장 약한 신호를 쓰게 된다.
이전 구현은 관절이 부족하면 `POSE_UNKNOWN`을 내며 **이미 계산해둔 bbox 신호까지 버렸다.**

**부동은 단독으로 `FALLEN`을 만들지 않는다.** 가만히 서 있는 사람도 부동이다.
가중치를 낮게 두어 형상이 수평일 때 확신을 올리는 보조로만 쓴다. 테스트로 고정했다.

### 점수화

각 신호를 임계값 기준 시그모이드로 0~1에 매핑해 가중 평균한다. 이산 투표에서는
54도와 10도가 똑같이 "표 없음"이었지만 점수는 이를 구분한다.

출력에 함께 싣는 값:
- `fallen_score` (0~1) — **보정된 확률이 아니다.** 임계값에서의 거리를 편 값이다
- `signal_count` (1~4) — 판정에 실제로 쓴 신호 수. 작으면 관절 없이 판정한 것이다

라벨이 이진이라 확신도가 사라지는 것을 이 두 값이 막는다.

**⚠️ 임계값과 가중치는 실측 근거가 없다.** `torso_horizontal_deg: 55`,
`bbox_aspect_ratio: 1.20`, `vertical_extent_ratio: 0.25`, 가중치 4개, 시그모이드 폭 3개가
전부 임의값이다. 명세가 정한 것은 "수직/수평에 가까운 상체·박스 비율"이라는 **방향**이지
숫자가 아니다. 라벨 데이터 확보 후 **로지스틱 회귀**(입력 4개, 파라미터 5개)로 교체하며,
그때 `fallen_score`가 보정된 확률이 된다. 그 전까지 이 값들을 "검증됨"으로 보고하지 않는다.

### 자세 흔들림 완충 (PostureSmoother)

누우면 팔다리가 가려져 점수가 출렁인다. 최근 N프레임 다수결로 라벨을 완충한다.
**완충은 라벨만 바꾸며 `fallen_score`와 `signal_count`는 그대로 싣는다.** 완충이 근거를 감추면 안 된다.
원본 판정은 `signals.raw_status`에 남긴다.

---

## 16. persistence 규칙

**이벤트 트리거는 사람 관측 시간이다. 자세가 아니다.**

- 사람을 `person_confirm_seconds`(1.0초) 이상 안정 관측하면 encounter를 확정한다(명세 25.1).
- 자세와 무관하게 확정한다. `NORMAL`도 보고 대상이다.
- `FALLEN` 지속 시간(`fallen_seconds`, 1.5초)은 **심각도 속성**으로 따로 잰다(명세 25.6).
- timestamp 기반으로 판정한다. 파일 입력은 영상 내 시간, 카메라는 벽시계 경과 시간을 쓴다.
- **trackId 단위로 관리한다.** 프레임 단위로만 세면 A가 0.5초, B가 0.5초 관측됐을 때
  "1초 연속"으로 오판한다.
- 같은 트랙 재발행은 `event_cooldown_seconds`(15초)로 억제한다(명세 25.4의 중복 판정과 정합).

### 기억 시간 (memory.forget_seconds)

사람을 "기억"하는 시간은 **`forget_seconds` 하나로 통일**한다. 이 값이 셋을 모두 결정한다.

1. 트래커가 같은 trackId를 유지하는 시간 (tracker의 `track_buffer`)
2. ID가 바뀌었을 때 누적 시간을 승계하는 시간
3. 트랙 상태를 폐기하는 시간

**`track_buffer`는 프레임 단위이므로 실측 FPS로 매 프레임 자동 환산한다.** 고정하면 실제 기억
시간이 FPS에 따라 출렁인다(실측: 같은 설정이 25초~91초). 환산 후에는 FPS가 3배 달라져도 10초를 유지한다.

규칙은 한 문장이다: **"forget_seconds 안에 돌아오면 같은 사람, 넘으면 새 사람."**

### ID 교체 시 상태 승계

트래커가 새 trackId를 부여해도 **시간·위치가 가까우면** 누적 시간을 승계한다.
명세가 허용하는 범위다(`docs/07-AI-탐지-음성.md` 25.4 "시간·위치·외형의 단순 조건으로 병합").
**외형은 쓰지 않는다.** 승계 오작동을 막기 위해 세 가지를 테스트로 고정했다 —
멀리 떨어진 새 ID 승계 금지, 오래된 트랙 승계 금지, 한 상태의 중복 승계 금지.

---

## 17. JSONL 로그 스키마

로그는 두 파일로 분리한다.

| 파일 | 내용 | 기본 |
|---|---|---|
| `events.jsonl` | encounter 이벤트. **명세 31-5 공통 봉투 형식** | 항상 기록 |
| `frames.jsonl` | 프레임 단위 상세(판정 신호 포함). 임계값 조정용 | `--frame-log`일 때만 |

출력은 실행마다 타임스탬프 하위 폴더에 쓴다(`runs/<name>/20260729_121929/`).
이미지는 누적되는데 JSONL만 덮어써지면 증빙이 어긋나기 때문이다.

> ### ⚠️ encounter 발급 권한 (2026-07-30 확인)
>
> **encounter 발급은 Mission Manager 단독 권한이다(명세 26.1).** detection이 만드는 것은
> `/perception/person_candidates`의 **후보**까지이며, `encounterId`를 생성하지 않는다.
> `src/candidates.py`와 `src/ros_main.py`는 이 원칙을 지킨다.
>
> 반면 `schemas.py`의 `build_encounter_data()`는 `events.jsonl`에 `ENCOUNTER_CONFIRMED`를
> 계속 기록한다. 이는 **로컬 검증 로그이지 관제로 나가는 메시지가 아니다** — MQTT 발행은
> `cloud_bridge_node`가 담당하고 detection은 발행하지 않는다.
>
> 다만 같은 이름의 메시지 타입이 두 곳에서 생성되는 모양이라 오해 소지가 있다.
> 아래 절의 스키마를 **로봇 통합 경로의 계약으로 착각하지 않는다.** 정리 여부는 §35 18번.

### events.jsonl — 명세 31-5 봉투 + 31-6 ENCOUNTER_CONFIRMED

```json
{
  "schemaVersion": "1.0",
  "messageId": "52ec69bc-ba0a-4567-812b-82af0fe28ae9",
  "messageType": "ENCOUNTER_CONFIRMED",
  "robotId": "SENTINEL-01",
  "missionId": null,
  "sequence": 1,
  "sentAt": "2026-07-29T03:19:38.183Z",
  "data": {
    "encounterId": "60aa4d35-73e5-477d-a1d4-a80cd67b9a55",
    "mapPose": null,
    "personCount": 2,
    "persons": [
      {"trackId": 1, "confidence": 0.9046, "poseStatus": "NORMAL",
       "fallenSec": null, "observedSec": 1.0},
      {"trackId": 2, "confidence": 0.8562, "poseStatus": "FALLEN",
       "fallenSec": 1.8, "observedSec": 3.2}
    ],
    "recordingState": null,
    "preBufferSec": null,
    "_local": { "frameIndex": 30, "source": "...", "eventImage": "...", "persons": [] }
  }
}
```

**채울 수 없는 필드는 `null`로 두고 값을 지어내지 않는다**(AGENTS.md §31).
- `mapPose` — SLAM/Nav2의 TF 변환 필요 (명세 25.2)
- `missionId` — Mission Manager 발급
- `recordingState`, `preBufferSec` — 이벤트 녹화 파이프라인 필요 (명세 32-6)

`_local`은 명세에 없는 로컬 부가 정보다. 봉투 본문을 오염시키지 않도록 분리했고,
MQTT 발행 시에는 제거하거나 백엔드와 합의한 뒤 보낸다.

로그 작성은 UTF-8, 한 줄당 하나의 JSON 객체, 즉시 flush한다.

---

## 18. 이벤트 이미지 저장 규칙

- 이벤트 이미지 파일 경로를 JSONL에 기록한다.
- 출력 폴더는 자동 생성한다.
- 파일명 충돌을 방지한다(예: ISO 8601 기반 또는 안전한 timestamp 이름 사용).
- 원본 프레임을 수정하지 않고 저장한다(`storage.py` 책임, §10 참고).

---

## 19. 코드 품질 및 코딩 컨벤션

- MVP에서는 과도한 추상화를 금지한다(§10 구현 우선순위 참고: 동작하는 코드 > 읽기 쉬운 코드 > 추상화).
- 기존 코드/파일을 우선 재사용하고 중복 생성하지 않는다.
- 현재 작업과 무관한 파일을 수정하지 않으며, 저장소 전체 포매팅이나 이유 없는 리팩터링을 하지 않는다.
- 모델 내부 구현(Ultralytics 등)을 직접 수정하지 않는다.
- placeholder, mock, `pass`만 있는 구현, TODO만 남기고 끝내는 구현을 금지한다(§31).
- dataclass/TypedDict 등 표준 도구로 충분한 경우 별도 schema 프레임워크를 도입하지 않는다.

---

## 20. CLI 설계 규칙

모든 스크립트는 `--help`로 사용법을 확인할 수 있어야 한다.

```bash
python <script> --help
```

예시 CLI(ISSUE-03):
```bash
python scripts/inspect_raw_data.py \
  --images <IMAGE_DIR> \
  --labels <LABEL_DIR> \
  --report data/interim/raw_data_report.json
```

`main.py`는 입력 영상, 모델 경로, threshold, 출력 경로를 CLI 인자로 받아 파이프라인을 실행하는
entry point 역할을 한다.

---

## 21. 오류 처리 규칙

- 손상 이미지, 누락 라벨, 읽을 수 없는 JSON 등은 예외로 처리하고 보고서에 기록한다(무시하지 않는다).
- 검증 스크립트는 오류 시 exit code 1, 정상 시 exit code 0을 반환한다(§12 validate_yolo_dataset.py).
- 잘못된 입력 파일, 출력 디렉터리 미존재 등은 통합 테스트 시나리오에 포함한다(§29).
- 로그 파일 쓰기 실패 등은 `logger.py`에서 명시적으로 처리한다(§10).
- 종료 시 리소스(비디오 캡처 등)를 해제한다.

---

## 22. 테스트 및 검증 규칙

### 기본 검증
```bash
python -m compileall src
python tests/test_posture_persistence.py
python tests/test_candidates.py
```

`tests/`에 `__init__.py`가 없어 `python -m unittest tests.xxx`는 실패한다.
**파일을 직접 실행하거나 `python -m pytest tests -q`를 쓴다**(pytest는 선택 의존성이며
개발 PC 환경에 설치되어 있지 않을 수 있다). 각 테스트 파일은 pytest 없이도 단독 실행된다.

`ros_main.py`는 `rclpy` 임포트가 필요해 개발 PC에서 실행·컴파일 검증만 가능하고
동작 검증은 Jetson에서 한다. 계약 로직은 ROS 의존이 없는 `candidates.py`로 분리되어
`test_candidates.py`가 개발 PC에서 검증한다.

모든 CLI는 `--help`로 확인한다.

### 데이터 검증
- 원본 데이터 검사 → YOLO 변환 → split → validator → label visualization → 수동 샘플 확인

### 모델 검증
- Detect 1 epoch smoke test
- Pose pretrained test
- 실제 출력 파일 확인, loss NaN/inf 확인, 결과 디렉터리 생성 확인

### 통합 검증
- 입력 영상 읽기, person 미탐지 시 Pose 미실행, person 탐지 시 crop
- keypoint 좌표 복원, posture 상태, persistence, JSONL, 이벤트 이미지, 종료 시 리소스 해제

### 결과 표현
테스트 결과는 반드시 다음으로 구분한다.
```text
PASS
FAIL
SKIPPED
BLOCKED
```
**실행하지 않은 테스트를 PASS로 표시하지 않는다.**

현재 `tests/` 디렉터리는 존재하지 않는다. ISSUE-10에서 통합 테스트를 추가할 때 생성한다.

---

## 23. 성능 평가 지표

### Detect
Precision, Recall, mAP50, mAP50-95, false negative 사례, inference time, FPS.
재난 탐색 MVP에서는 **person false negative를 중요한 리스크**로 취급한다.

### Pose
person crop에서 keypoint 검출 성공률, 주요 관절 confidence, FPS, 누움/서기/앉기/숙이기 영상별 동작 여부.

### End-to-End
전체 FPS, Detect latency, Pose latency, event 확정 지연, 누운 사람 이벤트 저장 성공 여부.

Jetson 배포를 고려해 정확도뿐 아니라 메모리와 FPS를 함께 측정한다.

---

## 24. Git 브랜치 및 커밋 규칙

**현재 브랜치 상태(확인 완료):** 현재 브랜치는 `feat/ai/object-detection`이며, 아래 §26에서
제안하는 `feature/object-detection-data-pipeline`을 새로 만들 필요 없이 **현재 브랜치를 계속 사용**한다.
(지시서 원문은 새 브랜치를 제안하지만, 이미 적절한 브랜치가 있으므로 자동 생성하지 않는다는 규칙을 우선한다.)

- 자동 커밋 금지. 사용자가 명시적으로 요청한 경우에만 commit한다.
- commit 전에 `git diff`로 변경 내용을 확인한다.
- 데이터, 가중치, 비밀정보(API key 등)가 포함되지 않았는지 확인한다.
- 한 커밋에는 하나의 논리적 변경만 담는다.
- 기존 브랜치를 임의 삭제하지 않는다. force push 금지.
- 작업과 무관한 변경을 stage하지 않는다.

권장 커밋 메시지 예:
```text
docs: define detection and pose MVP requirements
docs: document detection dataset selection
feat: add raw detection dataset inspection
feat: implement YOLO dataset preprocessing pipeline
feat: add detection training and pose smoke tests
feat: integrate detection and pose inference
feat: add rule-based posture classification
feat: add JSONL event logging and image storage
test: add end-to-end detection pipeline tests
docs: document object detection MVP
```

---

## 25. Jira Epic과 Issue 정의

### Epic
```text
객체탐지 기능 구현
```

| Issue | 목적 |
|---|---|
| ISSUE-01 | Detect→Pose 흐름, person trigger, posture state, 입출력 정의 |
| ISSUE-02 | 객체탐지 데이터셋 선정 및 Pose 테스트 영상 정의 |
| ISSUE-03 | 객체탐지 데이터 확보·품질 검사 및 Pose 테스트 영상 준비 |
| ISSUE-04 | 객체탐지 데이터 전처리 및 YOLO 형식 변환 |
| ISSUE-05 | 객체탐지 baseline 학습 및 pretrained Pose 검증 |
| ISSUE-06 | threshold 조정 및 여유가 있으면 TensorRT FP16 |
| ISSUE-07 | Detect → Crop → Pose → Rule 통합 파이프라인 |
| ISSUE-08 | 최소 로그 구현 |
| ISSUE-09 | JSONL 및 이벤트 이미지 저장 |
| ISSUE-10 | 통합 테스트 및 문서화 |

각 Issue는 구현 시 다음 하위 항목을 함께 기록한다: 목적 / 입력 / 출력 / 구현 파일 / 완료 조건 /
테스트 / 차단 요인 / 범위 제외 / 권장 커밋.

**중요: Issue 번호는 착수 순서가 아니다.** §26의 순서 변경에 따라 실제 진행 순서는 다음과 같다.

```text
ISSUE-01, 02 (문서)
→ ISSUE-07 (파이프라인)
→ ISSUE-08, 09 (로깅·저장)
→ §28.1 통합 연결 검증 게이트
→ ISSUE-03, 04 (데이터)
→ ISSUE-05, 06 (학습·threshold)
→ ISSUE-10 (통합 테스트·문서)
```

ISSUE-03/04/05는 AI-Hub 데이터에 의존하므로 확보 여부에 따라 `BLOCKED` 처리될 수 있다.
나머지 Issue는 데이터 없이 완료 가능하다.

### Jira 실제 진행 상태 (2026-07-30 기준)

| Jira | 대응 | 상태 |
|---|---|---|
| S15P11A301-93 | ISSUE-01 요구사항·완료 기준 정의 | 완료 |
| S15P11A301-94 | ISSUE-02 데이터셋 선정 | 진행 중 (AI-Hub 대기) |
| S15P11A301-95 | ISSUE-03 데이터 확보·품질 검사 | 진행 중 (AI-Hub 대기) |
| S15P11A301-96 | ISSUE-04 전처리·YOLO 변환 | 진행 중 (AI-Hub 대기) |
| S15P11A301-97 | 베이스라인 모델 구축 | 완료 (사전학습 기준) |
| S15P11A301-98 | ISSUE-05/06 학습·threshold | 진행 중 — **학습·정량 성능 검증까지 해야 완료로 본다** |
| S15P11A301-99 | ISSUE-07 추론 모듈 구현 | 완료 |
| S15P11A301-100 | ISSUE-08 로그 구조 설계 | 완료 |
| S15P11A301-101 | ISSUE-09 결과 저장 파이프라인 | 완료 |
| S15P11A301-102 | ISSUE-10 통합 테스트·문서화 | 완료 |

**주의: 완료 처리한 Issue는 모두 사전학습 모델 기준이다.** 파인튜닝·정량 성능(mAP 등) 검증은
S15P11A301-98에 남아 있으며, 데이터 확보 전에는 이 항목을 완료로 바꾸지 않는다(§22 BLOCKED 규칙).

---

## 26. 화요일 구현 계획

> ### ⚠️ 실행 순서 변경 (2026-07-28 결정)
>
> **기존 계획: 데이터 → 학습 → 파이프라인** → **변경: 파이프라인 → 연결 검증 게이트 → 데이터 → 학습**
>
> **변경 이유**
> - 추론 파이프라인이 다루는 것은 **Ultralytics의 출력 형식**이지 AI-Hub의 라벨 형식이 아니다.
>   즉 파이프라인 구현은 **AI-Hub 데이터에 전혀 의존하지 않는다.**
> - `yolo26n.pt`(COCO 사전학습)는 이미 **person(class 0)** 을 탐지하고, `yolo26n-pose.pt`는
>   사람 keypoint를 바로 출력한다. **자체 학습 모델 없이 end-to-end 파이프라인을 완성·검증할 수 있다.**
> - 기존 순서는 "AI-Hub 데이터가 제때 도착한다"에 스프린트 전체를 걸었다. 신청·승인이 하루만 밀려도
>   금요일에 보여줄 결과물이 없다. 변경된 순서에서는 **데이터가 끝내 오지 않아도 데모가 동작한다.**
> - §10 구현 우선순위("동작하는 코드 > 읽기 쉬운 코드 > 추상화")와 §30 Must 우선순위에도 더 부합한다.
>
> **학습으로 넘어가는 조건:** §28의 **통합 연결 검증 게이트**를 통과해야 한다.
> 게이트 통과 전에는 ISSUE-04/05(변환·학습)에 착수하지 않는다.
>
> **핵심 설계 제약:** `object_detector.py` / `pose_estimator.py`는 **모델 경로를 인자로 받는다.**
> 나중에 파인튜닝 가중치로 교체하는 비용이 0이어야 한다. 모델 경로를 하드코딩하면 이 계획 전체가 무너진다.

### 요일별 배치 요약

| 요일 | 주제 | Issue |
|---|---|---|
| 화 | 문서 확정 + 파이프라인 골격 (사전학습 모델) | 01, 02, 07 착수 |
| 수 | 파이프라인 완성 + 로깅/저장 | 07 완료, 08, 09 |
| 목 | **연결 검증 게이트** → 통과 시 데이터 작업 | 게이트, 03, 04 |
| 금 | 학습 + threshold + 통합 테스트 + 문서 | 05, 06, 10 |

**데이터 확보(§11.1)는 화요일부터 병행한다.** 사용자가 AI-Hub 신청·다운로드를 진행하는 동안
에이전트는 파이프라인을 구현한다. 두 작업은 서로를 기다리지 않는다.

### 목표
```text
ISSUE-01, ISSUE-02, ISSUE-07 착수
(데이터 확보는 사용자가 병행 진행)
```

### 브랜치
현재 브랜치 `feat/ai/object-detection`을 그대로 사용한다(§24 참고).

### ISSUE-01 — `../../docs/ai/detection/requirements.md`
기존 초안이 있으므로 삭제 후 재작성하지 말고 병합·정합화한다. 필수 내용: 프로젝트 목적,
Detect-first 파이프라인, 입력과 출력, person class, Pose trigger, confidence 초기값,
최소 bbox 크기, 자세 상태, 주요 keypoint, Definition of Done, 제외 범위.

**정합화 시 반드시 반영할 것(§6 결정):**
- 문서를 "이번 스프린트 범위(person 단일)"와 "다음 스프린트 이월(장애물, Exit/소화기/위험표지)"
  두 절로 명확히 분리한다. 기존 4종 클래스 기술을 **삭제하지 말고 이월 절로 옮긴다.**
- 자세 상태는 명세 25.6의 `NORMAL` / `FALLEN` 2값을 그대로 쓴다(§15).
- **이벤트 트리거는 사람 관측 시간이지 자세가 아님**을 명시한다(§16).
- 장애물 탐지는 LiDAR/Nav2와 역할이 중복될 수 있음을 명시한다(§6의 역할 분담 표 참조).

초기 Pose trigger 예(초기값, ISSUE-06에서 조정):
```text
class_name == "person"
confidence >= 0.50
bbox_width >= 80 px
bbox_height >= 80 px
```

### ISSUE-02 — `../../docs/ai/detection/dataset_selection.md`(신규)
필수 내용: 실제 확인한 데이터셋 후보, 출처, bbox 포함 여부, 이미지 수, 라벨 형식,
재난/실내/저조도 유사성, 라이선스, 변환 난이도, 장단점, 최종 선정 또는 보류(보류 시 필요 정보), class map.

#### AI-Hub 후보 데이터셋 (2026-07-28 공식 페이지에서 실제 확인)

아래는 AI-Hub 데이터셋 상세 페이지에서 직접 확인한 사실이다. **추정이 아닌 확인된 값만 기재했으며,
페이지에 명시되지 않은 항목은 `미상`으로 남겼다.** 최종 선정은 사용자가 결정한다.

| # | 데이터셋 | dataSetSn | 구축 | 규모 | 라벨 | bbox | 이용조건 | 판정 |
|---|---|---|---|---|---|---|---|---|
| A | 스마트 제조 시설 안전 감시를 위한 데이터 | 71679 | 2023 | 영상 20,000 / 이미지 400,000 / 라벨 420,000 | JSON (mp4, png) | ✅ 바운딩박스(이미지) | 일반 다운로드, 내국인, API 제공 | **1순위 추천** |
| B | 1인칭 시점 보행영상 | 159 | 2020 | 이미지 757,653 / 클립 18,680 | JSON (png, mp4) | ✅ 전체의 70% | 저작권 해결·재사용 제한 없음, 내국인 | 2순위 |
| C | 지능형 관제 서비스 CCTV 영상 데이터 | 71850 | 2024 | 300건 | JSON | 미상 | 일반 다운로드, 내국인 | 보조 검증용 |
| D | 낙상사고 위험동작 영상-센서 쌍 데이터 | 71641 | 2023 | 영상 22,672 / 이미지 226,720 | JSON, 키포인트(영상)+BBOX(이미지) | ✅ | ❌ **안심존 + IRB 심의 결과 통지서 필요** | **사용 불가** |

**후보별 판단 근거**

- **A (71679, 1순위)** — `object_information_human` 필드로 **사람 객체 정보를 명시적으로 포함**하고,
  바운딩박스 라벨링이 확정되어 있으며, 40만 장 규모에 일반 다운로드가 가능하다. 실내 산업 시설 환경이라
  **밀폐공간 탐색이라는 프로젝트 목적과 도메인이 가장 가깝다.** 일반 CCTV와 **열화상 CCTV**를 함께
  포함해 저조도 대응 검토에도 유리하다.
- **B (159, 2순위)** — 규모가 가장 크고 저작권 제약이 가장 느슨하다. 다만 **1인칭 보행 시점(실외 보도 중심)**
  이라 UGV 카메라 시점과는 유사하지만 재난·실내 환경과는 거리가 있다. A가 막힐 경우의 대안, 또는
  person 다양성 보강용 추가 데이터로 활용한다.
- **C (71850, 보조)** — 안전사고 6종(침입, 싸움, **쓰러짐**, 군집, 인파밀집, 침수) 중
  **쓰러짐 이벤트 영상이 45건(15%)** 포함되어 있다. 300건으로 학습에는 부족하지만,
  **쓰러짐 판정 검증용 실제 영상**으로 가치가 크다.
  ISSUE-03의 Pose 테스트 영상(lying) 확보처로 우선 검토한다.
- **D (71641, 사용 불가)** — 내용만 보면 keypoint와 BBOX를 모두 갖춘 최적의 데이터셋이지만,
  **보건의료 데이터로 안심존을 통해서만 개방되고 IRB 심의 결과 통지서가 필요하다**(§11.1).
  로컬 다운로드·학습이 불가능하므로 이번 스프린트에서 제외한다. **내용이 좋다는 이유로 무리하게
  시도하지 말 것.** 이 판단 근거를 `../../docs/ai/detection/dataset_selection.md`에 반드시 기록한다.

**확인 필요 (신청 전 사용자가 페이지에서 직접 볼 것)**
- A와 B의 **JSON 라벨 스키마 실제 구조** — 필드명, bbox 좌표계(xyxy/xywh/절대/정규화)는 페이지 요약만으로
  알 수 없다. 샘플 데이터를 먼저 받아 `inspect_raw_data.py`로 확인한 뒤 `convert_to_yolo.py`를 작성한다
  (§31: 실제 데이터 구조를 보지 않고 parser 작성 금지).
- A의 **person 클래스 라벨명과 클래스 체계** — `object_information_human` 하위 구조 미확인.
- C의 **bbox 포함 여부** — 페이지에 명시되지 않음. 없으면 학습용이 아닌 시각 검증용으로만 사용.
- 각 데이터셋의 **실제 다운로드 용량** — 디스크 여유 공간 계획에 필요(§11.1, 2~3배 규칙).

**권장 진행:** 전체를 받기 전에 각 데이터셋의 **샘플 데이터를 먼저 내려받아** 라벨 구조를 확인하고,
변환 스크립트가 동작하는 것을 확인한 뒤 본 데이터를 신청한다. 이 방식이 수백 GB를 받고 나서
스키마가 안 맞아 재작업하는 위험을 없앤다.

#### Pose 테스트 영상
standing / sitting / bending / lying.
권장 조건: 영상당 5~15초, 사람 1명, 가능하면 전신 노출, 다양한 거리와 각도, 사용 권한 확인.
lying 영상은 위 후보 C(쓰러짐 이벤트 45건)에서 확보를 우선 검토하고, 부족하면 팀원이 직접 촬영한다
(직접 촬영 시 촬영 동의를 확보하고 `data/pose_test`에 배치, Git 커밋 금지).

### ISSUE-07 착수 — 파이프라인 골격 (사전학습 모델 기준)

**AI-Hub 데이터 없이 진행한다.** 사전학습 `yolo26n.pt` / `yolo26n-pose.pt`를 사용한다.

구현 대상:
```text
src/schemas.py            # Detection, Pose, Posture, Event 구조 (§10)
src/object_detector.py    # 모델 경로를 인자로 받음, person 필터링 (§10, §11 확장성)
src/pose_estimator.py     # person crop 입력, 원본 좌표 복원 메타데이터 (§10)
src/main.py               # CLI entry point (§20)
```

이번 단계 완료 조건:
- 입력 영상에서 person bbox가 출력된다.
- person crop에 Pose가 적용되어 keypoint가 나온다.
- keypoint가 **원본 프레임 좌표계로 복원**된다(crop 좌표 그대로 두지 않는다).
- `python src/main.py --help`가 동작한다.

**주의:** 이 시점에는 posture 판정·persistence·로깅이 아직 없다. 여기까지만 만들고 다음으로 넘어간다.

### 테스트 영상 준비 (병행, 사용자)

파이프라인 검증에 사람이 나오는 영상이 필요하다. AI-Hub와 무관하게 확보 가능하다.
standing / sitting / bending / lying 4종, 각 5~15초 (§26 Pose 테스트 영상 조건).
직접 촬영 시 촬영 동의를 확보하고 `data/pose_test`에 배치하며 Git에 커밋하지 않는다.

**이 영상이 없으면 수요일 이후 검증이 전부 막힌다. 화요일 중 확보를 우선한다.**

---

## 27. 수요일 구현 계획

### 목표
```text
ISSUE-07 완료, ISSUE-08, ISSUE-09
→ 사전학습 모델 기준 end-to-end 동작 확보
```

여전히 **AI-Hub 데이터에 의존하지 않는다.**

### ISSUE-07 완료
```text
Video → Detect → Person filter → Crop → Pose → Rule
```
`src/posture_classifier.py`, `src/inference_pipeline.py` 추가.
필수 결과: bbox, keypoints, posture state, overlay visualization.
자세 판정과 persistence는 §15·§16을 따른다. threshold는 설정/상수로 분리한다(§15).

### ISSUE-08 / ISSUE-09
`src/logger.py`, `src/storage.py` 추가.
최소 로그 및 JSONL/이벤트 이미지 저장 규칙은 §17·§18을 따른다.

### 수요일 종료 시점의 상태
```text
영상을 넣으면 → 사람을 찾고 → 자세를 판정하고 → 약 1초 지속되면
→ JSONL과 이벤트 이미지를 저장하는 파이프라인이 동작한다
(단, detector는 아직 COCO 사전학습 모델)
```

---

## 28. 목요일 구현 계획

### 목표
```text
통합 연결 검증 게이트 통과 → ISSUE-03, ISSUE-04
```

### 28.1 통합 연결 검증 게이트 (필수 관문)

**이 게이트를 통과하기 전에는 데이터 변환·학습(ISSUE-04/05)에 착수하지 않는다.**
연결이 안 된 상태에서 학습을 시작하면, 나중에 문제가 생겼을 때 원인이 모델인지 배선인지 구분할 수 없다.
먼저 배선을 확정하고, 그다음 모델을 바꿔 끼운다.

검증 항목 — 각각 `PASS` / `FAIL` / `SKIPPED` / `BLOCKED`로 기록한다(§22).
**실행하지 않은 항목을 PASS로 적지 않는다.**

| # | 검증 항목 | 통과 기준 |
|---|---|---|
| 1 | 입력 영상 읽기 | 프레임 수가 정상적으로 카운트됨 |
| 2 | person 미탐지 시 Pose 미실행 | 사람 없는 영상에서 Pose 호출 0회 |
| 3 | person 탐지 → crop | crop 이미지가 bbox 영역과 일치 |
| 4 | keypoint 좌표 복원 | overlay가 원본 프레임의 사람 위에 정확히 겹침 |
| 5 | posture 판정 | lying 영상에서 `FALLEN` 출력 |
| 6 | persistence | 약 1초 지속 시에만 이벤트 확정, 짧은 눕기는 무시 |
| 7 | JSONL 기록 | 한 줄당 JSON 1개, UTF-8, 스키마(§17) 일치 |
| 8 | 이벤트 이미지 저장 | 파일 생성 + JSONL의 경로와 실제 파일 일치 |
| 9 | 출력 디렉터리 자동 생성 | 미존재 경로 지정 시 생성됨 |
| 10 | 리소스 해제 | 종료 시 비디오 핸들 해제, 예외 없이 종료 |
| 11 | 모델 경로 교체 가능성 | `--model` 인자로 다른 가중치를 지정해 실행됨 |

**11번이 특히 중요하다.** 이 항목이 FAIL이면 파인튜닝 가중치로 교체할 수 없어 계획 전체가 무효가 된다.

게이트 결과는 §33 보고 형식으로 기록하고, **FAIL 항목이 있으면 데이터 작업으로 넘어가지 않고 먼저 고친다.**

### 28.2 게이트 통과 후 — ISSUE-03

`scripts/inspect_raw_data.py`, `docs/data_quality.md`(모두 신규).
검사 기능: 이미지 수, 라벨 수, 이미지/라벨 매칭, 누락 이미지/라벨, 손상 이미지, 읽을 수 없는 JSON,
해상도 통계, 클래스 빈도, bbox 개수, 라벨 구조 샘플, JSON 보고서.

**선행 조건:** `data/raw`에 AI-Hub 데이터가 실제로 존재해야 한다. 없으면 `BLOCKED`로 보고한다.

### 28.3 ISSUE-04

`scripts/convert_to_yolo.py`, `split_dataset.py`, `validate_yolo_dataset.py`,
`visualize_labels.py`, `configs/dataset.yaml`(모두 신규, §12 참고).

**선행 조건:** 실제 라벨 JSON을 열어 bbox 좌표계(xyxy/xywh, 절대/정규화)를 확인했을 것.
확인 전에는 parser를 작성하지 않는다(§31).

### 28.4 데이터가 도착하지 않은 경우

목요일까지 AI-Hub 데이터가 확보되지 않으면 ISSUE-03/04를 `BLOCKED`로 보고하고,
**남은 시간을 ISSUE-06(threshold 조정)과 ISSUE-10(통합 테스트·문서)에 투입한다.**
이 경우에도 사전학습 모델 기반 데모는 완성되어 있으므로 스프린트 산출물은 존재한다.
**데이터를 기다리며 대기하지 않는다.**

---

## 29. 금요일 구현 계획

### 목표
```text
ISSUE-05, ISSUE-06, ISSUE-10
(ISSUE-05는 데이터가 확보된 경우에만)
```

### ISSUE-05 — 학습 및 모델 교체
`scripts/train_detect.py`, `scripts/test_pose.py`(§13·§14 참고).

1. smoke test(`epochs=1`)로 학습 경로가 동작하는지 먼저 확인한다.
2. baseline(`epochs=20`) 학습을 실행한다.
3. 결과 확인: train/val loss, precision, recall, mAP50, mAP50-95, person false negative 샘플.
4. **파이프라인의 `--model` 인자를 학습 가중치로 교체해 §28.1 게이트를 다시 통과시킨다.**
   교체 후 게이트가 깨지면 학습 결과가 아니라 배선 문제이므로 즉시 원인을 보고한다.

**학습 시간이 남은 시간을 초과할 것 같으면 smoke test만 수행하고 baseline은 다음 스프린트로 넘긴다.**
장시간 학습을 실행했다고 허위 보고하지 않는다(§31).

### ISSUE-06 — threshold 조정
Detect confidence threshold, NMS IoU threshold, 최소 bbox 크기, Pose confidence threshold,
keypoint confidence threshold, FPS 측정. **person false negative 최소화를 우선**한다(§23).
시간이 남으면 TensorRT FP16(INT8 제외).

### ISSUE-10 — 통합 테스트 및 문서

### 필수 테스트 시나리오
```text
사람이 없는 영상
서 있는 사람 / 앉은 사람 / 몸을 숙이는 사람 / 누운 사람
짧게 누웠다가 일어나는 경우
약 1초 이상 누운 경우
일부 관절이 가려진 경우
작은 person bbox
저조도 또는 흔들림
잘못된 입력 파일
출력 디렉터리 미존재
```

### README 필수 내용
프로젝트 목적, MVP 범위, 설치 환경, 실행 방법, 데이터 준비, Detect 학습, Pose 테스트, 통합 실행,
출력 예시, JSONL schema, 알려진 한계, 다음 단계. (현재 `ai/detection`에는 전용 README.md가 없으므로
금요일에 신규 생성한다.)

---

## 30. 지연 시 우선순위

### Must
```text
1. person detect          (사전학습 모델로 충족 가능)
2. Pose trigger
3. keypoints
4. rule posture
5. visualization
6. JSONL 및 event image
7. §28.1 통합 연결 검증 게이트 통과
8. 문서와 테스트
```

**Must 항목은 전부 AI-Hub 데이터 없이 달성 가능하다**(§26 순서 변경). 자체 학습 detector는 Must가 아니다.

### Reduce
```text
Detect baseline 학습 20 epoch → smoke test 1 epoch만
Pose TensorRT
비디오 전체 저장
unknown 상태 고도화
multi-person
전체 keypoint 로그
```

### Drop
```text
Pose fine-tuning
ROS2
Dashboard
INT8
LiDAR fusion
```

Tracking(ByteTrack)은 Drop에서 제외되어 **Must에 포함**된다(§6 채택 결정).

---

## 31. AI Agent가 절대 하면 안 되는 작업

- 사용자의 확인 없이 패키지 설치
- requirements 변경
- CUDA/PyTorch 변경
- 원본 데이터 삭제 또는 수정
- 모델 가중치 삭제
- 대규모 파일 자동 다운로드
- 장시간 학습을 실행했다고 허위 보고
- 지원되지 않는 모델을 다른 모델로 조용히 대체
- 실제 데이터 구조를 보지 않고 parser 작성
- 테스트를 실행하지 않고 PASS 보고
- placeholder, mock, `pass`, TODO만 남기기
- API key, token, 개인정보 출력
- 저장소 전체 리팩터링
- 현재 작업과 무관한 코드 변경
- Git force 작업
- 사용자 요청 없는 commit/push
- Pose fine-tuning
- Tracking/ROS2/Dashboard/INT8 등 범위 밖 구현

---

## 32. 작업 시작 전 체크리스트

- [ ] `pwd`, `git rev-parse --show-toplevel`, `git branch --show-current`, `git status --short` 실행
- [ ] **`sentinel-yolo` 환경으로 실행하는지 확인** — `conda activate sentinel-yolo` 또는
      `C:/Users/SSAFY/miniforge3/envs/sentinel-yolo/python.exe` 절대경로 사용 (§7)
- [ ] `python`/`torch`/`cuda` 가용성 확인 (§7 환경 재확인 명령 실행)
- [ ] **데이터가 필요한 작업이면 `data/raw`에 실제 데이터가 있는지 먼저 확인** — 비어 있으면
      AI-Hub 확보 절차(§11.1)가 선행되어야 하며, 데이터 없이 parser를 추정 작성하지 않는다(§31)
- [ ] 이번에 다룰 Issue 번호와 목표 확인(§25)
- [ ] 관련 기존 파일/코드 유무 확인 후 재사용 여부 결정(§10)
- [ ] 실제 데이터 구조(원본 라벨 JSON 등)를 직접 열어 확인(추정 금지)
- [ ] 이번 작업 범위 밖 파일을 건드리지 않을 것인지 확인
- [ ] 구현 계획을 간단히 정리한 뒤 구현 시작

---

## 33. 작업 완료 후 보고 형식

```markdown
## 작업 요약

### 환경
- Repository root:
- Branch:
- Conda env: (sentinel-yolo 여부)
- Python:
- Torch:
- Torchvision:
- CUDA available:
- CUDA runtime:
- Ultralytics:
- OpenCV:

### Jira 상태
- ISSUE-01:
- ISSUE-02:
- ISSUE-03:
- ISSUE-04:
- ISSUE-05:
- ISSUE-06:
- ISSUE-07:
- ISSUE-08:
- ISSUE-09:
- ISSUE-10:

### 생성된 파일
- `path`: 설명

### 수정된 파일
- `path`: 설명

### 재사용한 기존 코드
- `path/function`: 설명

### 실행한 명령
```bash
실제로 실행한 명령만 기록
```

### 테스트 결과

#### PASS
- ...

#### FAIL
- ...

#### SKIPPED
- ...

#### BLOCKED
- ...

### 생성 산출물
- ...

### 알려진 문제
- ...

### 다음 우선순위
1. ...
2. ...
3. ...

### 권장 커밋 메시지
`type: message`
```

---

## 34. Definition of Done

> 아래는 §26의 실행 순서 변경(파이프라인 우선)에 맞춰 재정의한 기준이다.

### 화요일
`../../docs/ai/detection/requirements.md` 정합화, `../../docs/ai/detection/dataset_selection.md` 작성,
`schemas.py` / `object_detector.py` / `pose_estimator.py` / `main.py` 구현,
사전학습 모델로 **person bbox + keypoint(원본 좌표 복원)까지 출력**,
테스트 영상 4종(standing/sitting/bending/lying) 확보.

### 수요일
`posture_classifier.py` / `inference_pipeline.py` / `logger.py` / `storage.py` 구현,
rule classifier, persistence, JSONL, event image, overlay visualization,
**사전학습 모델 기준 end-to-end 실행 성공.**

### 목요일
**§28.1 통합 연결 검증 게이트 11개 항목 전부 PASS** (특히 11번 모델 경로 교체 가능성),
게이트 통과 후 `inspect_raw_data.py`, `data_quality.md`,
데이터 확보 시 `convert_to_yolo.py` / `split_dataset.py` / `validate_yolo_dataset.py` /
`visualize_labels.py` / `dataset.yaml`.

### 금요일
Detect smoke test(1 epoch) 실행, 가능하면 baseline(20 epoch),
**학습 가중치로 교체 후 게이트 재통과**, 최종 threshold, 통합 테스트, README, Jira 종료, 데모 실행 방법.

### 데이터 미확보 시의 최소 완료 기준

AI-Hub 데이터가 끝내 확보되지 않아도 아래는 반드시 충족한다. **이것이 이번 순서 변경의 목적이다.**
```text
사전학습 모델 기준으로
입력 영상 → person 탐지·추적 → 약 1초 안정 관측 → encounter 확정 → 조건부 Pose 자세 판정
→ JSONL + 이벤트 이미지 저장이 동작하고,
§28.1 게이트를 통과하며, README에 실행 방법이 기록되어 있다.
```
이 경우 ISSUE-03/04/05는 `BLOCKED`로 보고하고 다음 스프린트로 이월한다(§35).

### 전체 MVP
```text
입력 영상에서 person을 탐지·추적하고,
사람을 약 1초 안정 관측하면 encounter 이벤트를 확정하며,
조건부 Pose와 형상·부동 신호로 NORMAL / FALLEN을 판정해 속성으로 싣고,
명세 31-5 봉투 형식의 JSONL과 이벤트 이미지를 저장할 수 있다.
```

---

## 35. 다음 작업 인수인계 규칙

- 매 작업 종료 시 §33 보고 형식을 그대로 채워 남긴다.
- 다음 담당자(사람 또는 에이전트)는 이 보고를 읽고 §32 체크리스트부터 다시 시작한다.
- BLOCKED 항목은 원인과 필요한 의사결정을 명확히 남긴다(예: 모델 미지원, 데이터셋 미확정 등).
- 이 문서(`AGENTS.md`)와 실제 저장소 구조가 어긋나면, 다음 작업 시작 시 §8 저장소 분석 절차를
  다시 수행하여 이 문서를 갱신한다. 삭제 후 재작성하지 말고 기존 유효 규칙을 병합한다.

### 현재 미해결 항목 (2026-07-30 기준)

| # | 항목 | 성격 | 해결 주체 | 관련 |
|---|---|---|---|---|
| 1 | ~~AI-Hub 데이터셋 선정 및 다운로드~~ → **2026-08-05 해결.** AI-Hub 71550(전도 645클립)과 E-FPDS(6,982장) 확보·변환 완료 | 해결됨 | — | `docs/ai/detection/dataset_selection.md` |
| 1b | **Detect 파인튜닝 미채택 확정** — 교차 평가에서 사전학습을 이긴 조합 없음. 재시도 시 평가 함정 3건을 먼저 읽을 것 | 결정 완료 | — | `docs/ai/detection/finetuning_evaluation.md` |
| 2 | 선정 데이터셋의 실제 JSON 라벨 스키마 미확인 | ISSUE-04만 차단 (parser 작성 불가) | 샘플 확보 후 에이전트 | §12, §28 |
| 2b | **테스트 영상 4종(standing/sitting/bending/lying) 미확보** | **실영상 검증 차단** | 사용자(직접 촬영 가능) | §26, §28.1 |
| 2c | `FALLEN` 판정이 **실제 누운 사람 영상으로 미검증** | 임계값·가중치 신뢰도 미확보 | 2b 확보 후 | §15, §28.1 |
| 2d | MQTT 발행 미구현 (`paho-mqtt` 미설치, 브로커 정보 미확인) | 백엔드 연동 대기 | 사용자 + 백엔드 담당 | §11.1, 명세 31-4 |
| 3 | ~~클래스 범위 불일치~~ → **2026-07-28 해결: person 단일, 다중 클래스는 이월** | 해결됨 | — | §6 |
| 4 | ~~가중치 미다운로드~~ → **해결: yolo26n.pt / yolo26n-pose.pt / yolo26n-reid.onnx 확보** | 해결됨 | — | §13, §14 |
| 5 | `aihubshell`의 Windows 동작 여부 미확인 | 경미(대안 있음) | 사용자 | §11.1 |
| 6 | `notebooks/` 디렉터리 용도 불명 | 경미 | 팀 | §8 |
| 7 | **명세 이탈 2건(BoT-SORT, ReID) 미승인** | **팀 결정 필요** | 팀 | §0, §7.1 |
| 8 | `../../docs/ai/detection/requirements.md`가 최초 초안 상태(클래스 4종) | 문서 정합 | ISSUE-01 | §6 |
| 9 | ~~`requirements.txt`에 `lap`·`onnxruntime` 미반영~~ → **2026-08-05 해결.** 단, **Jetson(aarch64)은 마커에서 제외**했다. PyPI `onnxruntime-gpu`가 aarch64를 지원하지 않아 그냥 넣으면 CPU 빌드가 조용히 깔린다. NVIDIA Jetson 인덱스에서 수동 설치해야 한다 | 부분 해결 | Jetson 담당 | §7 |
| 10 | ~~자세 임계값이 실측 근거 없는 임의값~~ → **2026-08-05 부분 해결.** 자세·형상 임계값 5개 + 가중치 3개는 E-FPDS 정답 2,658건과 대조해 검증했다(쓰러짐 0.919 / 비쓰러짐 0.032로 분리). **변경 불필요** | 해결됨 | — | `docs/ai/detection/posture_threshold_calibration.md` |
| 10b | **부동(inactivity) 신호 3개는 미검증** — `inactivity_boost`, `motion.still_ratio`, `motion.full_still_seconds`. E-FPDS가 정지 이미지라 측정에서 빠졌다. "가구 위에 누워도 미동 없으면 쓰러짐" 판단을 담당하므로 재난 시나리오에서 중요하다 | 신뢰도 | **영상 데이터 필요** | §15, 위 문서 4절 |
| 11 | **Detect 상시 15FPS 미달** — Jetson 실측 9.45FPS(사람 4명, 스트리밍 동시 구동). 계측·벤치 도구는 준비 완료, **Jetson 실측 대기** | **성능 미달 확정** | ISSUE-06 | §7.2 |
| 12 | ~~Linux/aarch64 실행 이력 없음~~ → **2026-07-30 해결: Jetson 실기기 검증 완료** | 해결됨 | — | §7.1 |
| 13 | ~~JetPack 버전 미확인~~ → **해결: JetPack 6.x / L4T R36.4.7, torch 2.8.0 확인** | 해결됨 | — | §7.1 |
| 14 | ~~ROS2 노드 래핑 미착수~~ → **해결: `src/ros_main.py`(S15P11A301-153)** | 해결됨 | — | §10 |
| 15 | `ai/detection/README.md` 부재 — 외부 개발자 진입점이 AGENTS.md뿐 | 인수인계 | 에이전트 | §8 |
| 16 | 모델 가중치가 Git에 없어 clone만으로는 실행 불가 | 배포 | Runbook 3장으로 완화됨 | §7.1 |
| 17 | ~~`dataset_selection.md` 미작성~~ → **2026-07-31 해결**(`../../docs/ai/detection/dataset_selection.md`) | 해결됨 | — | §11.1 |
| 18 | `schemas.py`의 `build_encounter_data()`가 여전히 encounter를 만든다. **encounter 발급 권한은 Mission Manager 단독**(명세 26.1) | **중복 소지** | 에이전트 + Mission 담당 | §10, §17 |
| 19 | `src.main` 경로는 로봇에서 카메라를 열 수 없다(`usb_cam` 점유). 단독 검증 전용임을 코드가 강제하지 않는다 | 오용 위험 | 에이전트 | §10 |
| 20 | Jetson 가용 RAM 700MB 이하에서 `CUBLAS_STATUS_ALLOC_FAILED` 재현 | 운영 제약 | Jetson 담당 | Runbook 13장 |
| 21 | ~~기준 벤치 영상 부재~~ → **2026-07-31 해결: JetPack 영상 기반 결정적 재생성 절차**(04 25.7) | 해결됨 | — | §7.3 |
| 22 | ~~전원 모드 상향 미결~~ → **해결: 15W가 상한이라 조정 대상이 아님** | 해결됨 | — | §7.1 |
| 23 | 파인튜닝(ISSUE-05)과 TensorRT 변환의 순서 미정. **가중치가 바뀌면 엔진을 다시 구워야 한다**(약 425초) | 재작업 방지 | 사용자 | §7.3, §26 |
| 24 | **통합 상태 성능 미측정.** 16.23FPS는 AI 단독. 스트리밍·SLAM·녹화 경합 시 수치 불명 | **목표 달성 판정 차단** | Jetson 담당(A4 리허설) | §7.3 |
| 25 | `.engine`을 설정 기본값으로 올릴 시점 미정. 현재는 `--model` 인자 방식 | 배포 정책 | 팀 | §7.3 |

**1번과 2번은 ISSUE-03·04·05만 막는다.** §26의 순서 변경에 따라 ISSUE-01·02·07·08·09·10은
데이터 없이 진행 가능하며, 이것이 이번 스프린트의 Must 산출물이다(§30).

**2b번(테스트 영상)이 실질적으로 더 시급하다.** 이것이 없으면 파이프라인을 만들어도 검증할 수 없어
§28.1 게이트를 통과할 수 없다. AI-Hub와 무관하게 직접 촬영으로 즉시 해결 가능하므로 화요일 중 확보한다.

데이터가 없는 상태에서 변환·학습 스크립트를 작성해야 한다면, 검증 불가함을 명시하고 테스트 결과를
`BLOCKED`로 보고한다(§22). **PASS로 보고하지 않는다.**

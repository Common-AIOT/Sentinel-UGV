# Codex용 AGENTS.md 생성 프롬프트
## Sentinel UGV AIoT 캡스톤 프로젝트 완성형 마스터 문서 생성 지시서

아래 지시를 따라 현재 저장소의 루트에 **완성형 `AGENTS.md`**를 생성하라.

이 작업의 목적은 단순한 요약 문서가 아니라, 앞으로 Codex가 프로젝트의 화요일부터 금요일까지 모든 구현 작업을 일관되게 수행할 수 있도록 하는 **실무형 AI 개발 운영 문서**를 만드는 것이다.

설명만 출력하지 말고, 실제 저장소를 분석한 후 저장소 루트에 `AGENTS.md` 파일을 직접 생성하라.

---

# 1. 가장 먼저 수행할 작업

코드를 작성하기 전에 다음을 순서대로 수행하라.

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short
python --version
```

그다음 저장소 구조를 최대 깊이 3 수준으로 분석하라.

다음 폴더와 파일이 존재하는지 확인하라.

```text
src/
scripts/
configs/
docs/
data/
models/
runs/
tests/
README.md
requirements.txt
pyproject.toml
setup.cfg
.gitignore
AGENTS.md
CLAUDE.md
```

기존 `AGENTS.md`가 있으면 삭제하지 말고 내용을 확인한 뒤, 필요한 내용을 병합하여 완성형으로 교체하라.

기존 프로젝트 구조와 실제 파일명을 우선하며, 아래 지시의 예시 구조에 억지로 맞추지 마라.

---

# 2. 프로젝트 컨텍스트

`AGENTS.md`에 다음 프로젝트 정보를 명확히 포함하라.

## 프로젝트명

```text
Sentinel UGV
```

## 프로젝트 목적

재난·밀폐공간 탐색을 위한 AIoT 로봇 프로젝트다.

주요 하드웨어는 다음과 같다.

```text
RC car chassis
Jetson Orin Nano 8GB
Raspberry Pi
LiDAR
Camera
```

주요 소프트웨어 후보는 다음과 같다.

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

단, 이번 객체탐지 스프린트에서는 아래 범위만 구현한다.

```text
입력 영상
→ YOLO person 탐지
→ person crop
→ pretrained YOLO Pose 추론
→ keypoint 추출
→ 규칙 기반 자세 판정
→ normal / possible_fallen
→ 약 1초 persistence
→ JSONL 로그
→ 이벤트 이미지
→ 결과 시각화
```

이번 주에는 Pose fine-tuning을 수행하지 않는다.

---

# 3. 현재 개발 단계

`AGENTS.md`에 현재 개발 단계를 다음 의미로 기록하라.

```text
YOLO 기반 객체탐지 데이터 파이프라인 구축 및 베이스라인 모델 개발 단계
```

현재 상태를 다음처럼 구분하라.

## 완료된 기반 작업

- Git 저장소 및 프로젝트 기본 구조
- Python 및 CUDA 기반 개발환경
- 객체탐지 MVP 범위 정의
- Detect-first 파이프라인 설계

## 현재 진행 중

- 객체탐지 데이터셋 선정
- 원본 데이터 품질 검사
- YOLO 형식 변환
- 학습/검증/테스트 split
- Detect baseline 학습
- pretrained Pose 검증

## 이후 단계

- Detect → Crop → Pose 연결
- rule-based posture classifier
- persistence
- JSONL 및 이벤트 이미지 저장
- 통합 테스트
- README 및 Jira 마감

---

# 4. 개발 환경

업로드되거나 저장소에 존재하는 `requirements.txt`를 실제로 확인하고, `AGENTS.md`에 아래 환경을 기준 환경으로 명시하라.

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

다음 규칙을 반드시 포함하라.

- Python 버전을 임의로 변경하지 않는다.
- PyTorch, torchvision, CUDA 버전을 변경하지 않는다.
- `requirements.txt`를 임의로 덮어쓰지 않는다.
- `pip freeze > requirements.txt`를 실행하지 않는다.
- 새로운 라이브러리를 자동 설치하지 않는다.
- 새로운 패키지가 필요한 경우 기존 패키지로 해결 가능한지 먼저 검토한다.
- 설치가 꼭 필요하면 이유와 대안을 먼저 보고한다.
- TensorFlow, FastAPI, Gradio, STT, TTS 관련 패키지는 객체탐지 작업에 추가하지 않는다.
- `ultralytics`와 `opencv-python`은 현재 설치 버전을 우선한다.
- 정확한 버전이 고정되어 있지 않다고 해서 임의로 최신 버전으로 업그레이드하지 않는다.

---

# 5. 완성형 AGENTS.md의 필수 목차

생성하는 `AGENTS.md`는 최소한 다음 목차를 포함해야 한다.

```text
1. 문서 목적과 적용 범위
2. 프로젝트 개요
3. 현재 개발 단계
4. 최종 MVP 파이프라인
5. 이번 스프린트 범위
6. 제외 범위
7. 개발 환경
8. 저장소 분석 절차
9. 디렉터리 정책
10. 모듈 아키텍처
11. 데이터셋 관리 규칙
12. YOLO 데이터 변환 규칙
13. 객체탐지 모델 개발 규칙
14. Pose 모델 개발 규칙
15. 자세 판정 규칙
16. persistence 규칙
17. JSONL 로그 스키마
18. 이벤트 이미지 저장 규칙
19. 코드 품질 및 코딩 컨벤션
20. CLI 설계 규칙
21. 오류 처리 규칙
22. 테스트 및 검증 규칙
23. 성능 평가 지표
24. Git 브랜치 및 커밋 규칙
25. Jira Epic과 Issue 정의
26. 화요일 구현 계획
27. 수요일 구현 계획
28. 목요일 구현 계획
29. 금요일 구현 계획
30. 지연 시 우선순위
31. AI Agent가 절대 하면 안 되는 작업
32. 작업 시작 전 체크리스트
33. 작업 완료 후 보고 형식
34. Definition of Done
35. 다음 작업 인수인계 규칙
```

목차 이름은 자연스럽게 조정할 수 있지만 모든 내용은 빠짐없이 포함하라.

---

# 6. 저장소 분석 및 작업 규칙

다음 규칙을 `AGENTS.md`에 구체적으로 작성하라.

## 작업 시작 순서

```text
현재 경로 확인
→ Git 루트 확인
→ 브랜치 확인
→ 변경사항 확인
→ 프로젝트 구조 분석
→ 기존 코드 분석
→ 기존 테스트 분석
→ 실제 데이터 구조 분석
→ 구현 계획 작성
→ 구현
→ 검증
→ 결과 보고
```

## 기존 코드 우선

- 기존 코드가 있으면 재사용한다.
- 같은 기능의 파일을 중복 생성하지 않는다.
- 유사한 역할의 파일이 있으면 기존 파일을 확장한다.
- 기존 API를 불필요하게 변경하지 않는다.
- 현재 작업과 무관한 파일을 수정하지 않는다.
- 저장소 전체를 포매팅하지 않는다.
- 동작 중인 코드를 이유 없이 리팩터링하지 않는다.

## 구현 우선순위

```text
동작하는 코드
>
읽기 쉬운 코드
>
추상화
```

MVP에서는 과도한 추상화를 금지한다.

다음을 피한다.

```text
Factory Pattern
Plugin Framework
Generic Pipeline Framework
불필요한 Interface 계층
불필요한 Dependency Injection
과도한 클래스 상속
```

---

# 7. 권장 모듈 구조

실제 저장소 구조를 분석한 후, 아래 모듈이 필요한 시점과 역할을 `AGENTS.md`에 기록하라.

```text
src/object_detector.py
src/pose_estimator.py
src/posture_classifier.py
src/inference_pipeline.py 또는 src/pipeline.py
src/logger.py
src/storage.py
src/schemas.py
src/main.py
```

각 모듈의 책임을 다음과 같이 정의하라.

## object_detector.py

- Ultralytics Detect 모델 로딩
- 이미지/프레임 추론
- person 필터링
- confidence threshold
- bbox 반환
- 모델 내부 구현을 직접 수정하지 않음

## pose_estimator.py

- pretrained Pose 모델 로딩
- person crop 입력
- keypoint 추론
- 원본 프레임 좌표계로 복원할 수 있는 메타데이터 제공

## posture_classifier.py

- keypoint 기반 rule classifier
- 최소 상태: `normal`, `possible_fallen`
- 관절 부족 시 내부적으로 `unknown` 허용 가능
- 학습 모델이 아니라 명시적인 규칙 기반 구현

## inference_pipeline.py

- Detect → Filter → Crop → Pose → Rule → Persistence → Log → Save 연결
- 각 모듈을 호출하되 모델 구현을 중복 포함하지 않음

## logger.py

- JSONL 이벤트/프레임 로그 작성
- UTF-8
- 한 줄당 하나의 JSON 객체
- flush 및 파일 예외 처리

## storage.py

- 이벤트 이미지 저장
- 출력 디렉터리 생성
- 파일명 충돌 방지
- 원본 프레임 수정 금지

## schemas.py

- Detection, Pose, Posture, Event 데이터 구조 정의
- dataclass 또는 TypedDict 사용 가능
- 과도한 schema framework 도입 금지

## main.py

- CLI entry point
- 입력 영상, 모델, threshold, 출력 경로 설정
- 파이프라인 실행

---

# 8. Jira Epic과 Issues

`AGENTS.md`에 아래 Epic과 Issue를 자세히 포함하라.

## Epic

```text
객체탐지 기능 구현
```

## ISSUE-01

Detect→Pose 흐름, person trigger, posture state, 입출력 정의

## ISSUE-02

객체탐지 데이터셋 선정 및 Pose 테스트 영상 정의

## ISSUE-03

객체탐지 데이터 확보·품질 검사 및 Pose 테스트 영상 준비

## ISSUE-04

객체탐지 데이터 전처리 및 YOLO 형식 변환

## ISSUE-05

객체탐지 baseline 학습 및 pretrained Pose 검증

## ISSUE-06

threshold 조정 및 여유가 있으면 TensorRT FP16

## ISSUE-07

Detect → Crop → Pose → Rule 통합 파이프라인

## ISSUE-08

최소 로그 구현

## ISSUE-09

JSONL 및 이벤트 이미지 저장

## ISSUE-10

통합 테스트 및 문서화

각 Issue에 대해 다음 내용을 표 또는 하위 섹션으로 작성하라.

```text
목적
입력
출력
구현 파일
완료 조건
테스트
차단 요인
범위 제외
권장 커밋
```

---

# 9. 화요일 구현 계획

다음 내용을 매우 구체적으로 `AGENTS.md`에 작성하라.

## 화요일 목표

```text
ISSUE-01
ISSUE-02
ISSUE-03
ISSUE-04
ISSUE-05 착수
```

## 권장 브랜치

```text
feature/object-detection-data-pipeline
```

단, 현재 브랜치가 이미 적절하면 새 브랜치를 자동 생성하지 말고 현재 상태를 먼저 보고한다.

## ISSUE-01 대상 문서

```text
docs/requirements.md
```

필수 내용:

- 프로젝트 목적
- Detect-first 파이프라인
- 입력과 출력
- person class
- Pose trigger
- confidence 초기값
- 최소 bbox 크기
- 자세 상태
- 주요 keypoint
- Definition of Done
- 제외 범위

초기 Pose trigger 예:

```text
class_name == "person"
confidence >= 0.50
bbox_width >= 80 px
bbox_height >= 80 px
```

이 값은 초기값이며 ISSUE-06에서 조정하도록 기록한다.

## ISSUE-02 대상 문서

```text
docs/dataset_selection.md
```

필수 내용:

- 실제 확인한 데이터셋 후보
- 데이터셋 출처
- bbox 포함 여부
- 이미지 수
- 라벨 형식
- 재난/실내/저조도 유사성
- 라이선스 또는 사용 조건
- 변환 난이도
- 장단점
- 최종 선정 또는 선정 보류
- 선정 보류 시 필요한 정보
- class map

초기 클래스:

```python
CLASS_MAP = {
    "person": 0,
}
```

Pose 테스트 영상:

```text
standing
sitting
bending
lying
```

권장 조건:

```text
영상당 5~15초
사람 1명
가능하면 전신 노출
다양한 거리와 각도
사용 권한 확인
```

## ISSUE-03 구현

```text
scripts/inspect_raw_data.py
docs/data_quality.md
```

검사 기능:

- 이미지 수
- 라벨 수
- 이미지/라벨 매칭
- 누락 이미지
- 누락 라벨
- 손상 이미지
- 읽을 수 없는 JSON
- 해상도 통계
- 클래스 빈도
- bbox 개수
- 라벨 구조 샘플
- JSON 보고서

기본 CLI 예:

```bash
python scripts/inspect_raw_data.py \
  --images <IMAGE_DIR> \
  --labels <LABEL_DIR> \
  --report data/interim/raw_data_report.json
```

## ISSUE-04 구현

```text
scripts/convert_to_yolo.py
scripts/split_dataset.py
scripts/validate_yolo_dataset.py
scripts/visualize_labels.py
configs/dataset.yaml
```

### convert_to_yolo.py

- 실제 JSON 구조를 먼저 확인한다.
- person만 변환한다.
- bbox clipping
- invalid bbox 제거
- YOLO 좌표 정규화
- 변환 통계
- JSON 보고서

### split_dataset.py

- 기본 비율 0.8 / 0.1 / 0.1
- seed 42
- 이미지/라벨 pair 유지
- 가능하면 group/scene 단위 split
- group 정보가 없으면 데이터 누수 위험 문서화

### validate_yolo_dataset.py

- 라벨 값 5개
- class id 정수 및 범위
- 좌표 0~1
- width/height 양수
- 이미지/라벨 pair
- 오류 시 exit code 1
- 정상 시 exit code 0

### visualize_labels.py

- YOLO bbox를 픽셀 좌표로 복원
- 랜덤 샘플
- seed
- 결과 별도 저장
- 원본 수정 금지

### dataset.yaml

```yaml
path: ../data/processed
train: images/train
val: images/val
test: images/test

names:
  0: person
```

실제 상대경로가 저장소에서 올바른지 확인한다.

## ISSUE-05 착수

```text
scripts/train_detect.py
scripts/test_pose.py
```

### train_detect.py

Ultralytics API만 사용한다.

```python
from ultralytics import YOLO
```

기본:

```text
imgsz=640
optimizer="auto"
seed=42
plots=True
```

smoke test:

```text
epochs=1
```

baseline:

```text
epochs=20
```

프로젝트 계획상 Detect 모델은 `yolo26n.pt`를 가정하지만, 현재 설치된 Ultralytics가 실제 지원하는지 확인한다.

지원되지 않으면 임의로 모델을 바꾸고 성공했다고 하지 않는다.

### test_pose.py

프로젝트 계획상 Pose 모델은 `yolo26n-pose.pt`를 가정한다.

- pretrained inference만 수행
- 학습 금지
- source 이미지/영상
- confidence
- device
- 결과 저장
- 전체 프레임 수
- keypoint 검출 프레임 수
- keypoint shape
- 가능하면 FPS

---

# 10. 수요일 구현 계획

`AGENTS.md`에 다음 계획을 포함하라.

## 수요일 목표

- ISSUE-05 완료
- ISSUE-06
- ISSUE-07 착수

## ISSUE-05 완료

- Detect baseline 결과 확인
- train/val loss
- precision
- recall
- mAP50
- mAP50-95
- person false negative 샘플 확인
- pretrained Pose의 standing/sitting/bending/lying 영상 검증

## ISSUE-06

- Detect confidence threshold 조정
- NMS IoU threshold 검토
- 최소 bbox 크기
- Pose confidence threshold
- keypoint confidence threshold
- FPS 측정
- false negative 우선 최소화
- 시간이 남으면 TensorRT FP16
- INT8 제외

## ISSUE-07 착수

- object_detector.py
- pose_estimator.py
- schemas.py
- pipeline.py 초안
- person crop
- 좌표 변환
- 단일 person 기준 우선
- 다중 person은 시간이 남을 때

---

# 11. 목요일 구현 계획

## 목요일 목표

- ISSUE-07 완료
- ISSUE-08
- ISSUE-09
- 통합 실행

## ISSUE-07 완료

```text
Video
→ Detect
→ Person filter
→ Crop
→ Pose
→ Rule
```

필수 결과:

- bbox
- keypoints
- posture state
- overlay visualization

## 자세 판정

최소 상태:

```text
normal
possible_fallen
```

rule은 다음과 같은 관절 관계를 활용할 수 있다.

- shoulder midpoint
- hip midpoint
- knee positions
- torso angle
- bbox aspect ratio
- torso horizontalness
- hip/shoulder 높이 차
- keypoint confidence

단일 기준으로 판정하지 말고 최소 두 개 이상의 신호 조합을 권장한다.

정확한 threshold는 설정 파일 또는 상수로 분리한다.

## persistence

- `possible_fallen`이 약 1초 연속되면 이벤트 확정
- FPS가 알려진 경우 frame count 기반 가능
- timestamp 기반을 우선 고려
- 일시적 오탐을 필터링
- 상태가 normal로 복귀하면 카운터 또는 시작 시각 초기화

## ISSUE-08

최소 로그:

- timestamp
- frame index
- detections
- selected person
- posture
- persistence
- event 여부

## ISSUE-09

JSONL과 이벤트 이미지:

- 한 줄당 JSON 객체
- UTF-8
- 이벤트 이미지 파일 경로 기록
- 출력 폴더 자동 생성
- 파일명 충돌 방지
- 가능하면 ISO 8601 또는 안전한 timestamp 이름 사용

---

# 12. 금요일 구현 계획

## 금요일 목표

- ISSUE-10
- 통합 테스트
- threshold 최종 조정
- README
- Jira 종료

## 필수 테스트 시나리오

- 사람이 없는 영상
- 서 있는 사람
- 앉은 사람
- 몸을 숙이는 사람
- 누운 사람
- 짧게 누웠다가 일어나는 경우
- 약 1초 이상 누운 경우
- 일부 관절이 가려진 경우
- 작은 person bbox
- 저조도 또는 흔들림
- 잘못된 입력 파일
- 출력 디렉터리 미존재

## README 필수 내용

- 프로젝트 목적
- MVP 범위
- 설치 환경
- 실행 방법
- 데이터 준비
- Detect 학습
- Pose 테스트
- 통합 실행
- 출력 예시
- JSONL schema
- 알려진 한계
- 다음 단계

---

# 13. 지연 시 우선순위

다음 우선순위를 `AGENTS.md`에 포함하라.

## Must

1. person detect
2. Pose trigger
3. keypoints
4. rule posture
5. visualization
6. JSONL 및 event image
7. 문서와 테스트

## Reduce

- Pose TensorRT
- 비디오 전체 저장
- unknown 상태 고도화
- multi-person
- 전체 keypoint 로그

## Drop

- Tracking
- Pose fine-tuning
- ROS2
- Dashboard
- INT8
- LiDAR fusion

---

# 14. 데이터셋 관리 규칙

다음을 상세히 작성하라.

- 원본 데이터는 수정하지 않는다.
- `data/raw`는 read-only로 취급한다.
- 중간 산출물은 `data/interim`
- 최종 YOLO 데이터는 `data/processed`
- 시각화 샘플은 `data/samples`
- Pose 테스트 영상은 `data/pose_test`
- 원본 데이터와 모델 가중치는 Git에 추가하지 않는다.
- 라벨 변환은 재현 가능해야 한다.
- seed를 고정한다.
- 클래스 이름과 class id는 중앙에서 관리한다.
- 빈 라벨 이미지의 처리 정책을 명확히 한다.
- bbox를 이미지 범위로 clip한다.
- 아주 작은 bbox 제외 기준을 문서화한다.
- 중복 이미지 및 데이터 누수 가능성을 검사한다.
- 데이터셋 출처와 라이선스를 기록한다.

---

# 15. JSONL 스키마

`AGENTS.md`에 최소 권장 스키마를 제시하라.

예:

```json
{
  "timestamp": "2026-01-01T12:34:56.789Z",
  "frame_index": 123,
  "source": "input.mp4",
  "detections": [
    {
      "class_id": 0,
      "class_name": "person",
      "confidence": 0.91,
      "bbox_xyxy": [100.0, 50.0, 300.0, 450.0]
    }
  ],
  "pose": {
    "keypoints_xy": [[120.0, 80.0]],
    "keypoints_conf": [0.88]
  },
  "posture": "possible_fallen",
  "persistence_sec": 1.12,
  "event": true,
  "event_image": "outputs/events/20260101_123456_789.jpg"
}
```

단, 매 프레임 로그와 이벤트 로그를 별도 파일로 분리할 수 있음을 기록하라.

필드가 없는 경우 `null` 처리 또는 필드 생략 정책을 일관되게 정한다.

---

# 16. 테스트 및 검증 규칙

`AGENTS.md`에 아래 내용을 상세히 포함하라.

## 기본 검증

```bash
python -m compileall src scripts
```

모든 CLI:

```bash
python <script> --help
```

## 데이터 검증

- 원본 데이터 검사
- YOLO 변환
- split
- validator
- label visualization
- 수동 샘플 확인

## 모델 검증

- Detect 1 epoch smoke test
- Pose pretrained test
- 실제 출력 파일 확인
- loss NaN/inf 확인
- 결과 디렉터리 생성 확인

## 통합 검증

- 입력 영상 읽기
- person 미탐지 시 Pose 미실행
- person 탐지 시 crop
- keypoint 좌표 복원
- posture 상태
- persistence
- JSONL
- 이벤트 이미지
- 종료 시 리소스 해제

## 결과 표현

테스트 결과는 반드시 다음으로 구분한다.

```text
PASS
FAIL
SKIPPED
BLOCKED
```

실행하지 않은 테스트를 PASS로 표시하지 않는다.

---

# 17. 성능 지표

다음을 포함하라.

## Detect

- Precision
- Recall
- mAP50
- mAP50-95
- false negative 사례
- inference time
- FPS

재난 탐색 MVP에서는 person false negative를 중요한 리스크로 취급한다.

## Pose

- person crop에서 keypoint 검출 성공률
- 주요 관절 confidence
- FPS
- 누움/서기/앉기/숙이기 영상별 동작 여부

## End-to-End

- 전체 FPS
- Detect latency
- Pose latency
- event 확정 지연
- 누운 사람 이벤트 저장 성공 여부

Jetson에서는 정확도뿐 아니라 메모리와 FPS를 함께 고려한다.

---

# 18. Git 및 커밋 규칙

다음을 포함하라.

- 자동 커밋 금지
- 사용자가 요청한 경우에만 commit
- commit 전에 `git diff` 확인
- 데이터, 가중치, 비밀정보 포함 여부 확인
- 한 커밋에는 하나의 논리적 변경
- 기존 브랜치를 임의 삭제하지 않음
- force push 금지
- 작업과 무관한 변경을 stage하지 않음

권장 커밋:

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

# 19. AI Agent 금지 사항

다음을 강하게 명시하라.

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
- placeholder, mock, pass, TODO만 남기기
- API key, token, 개인정보 출력
- 저장소 전체 리팩터링
- 현재 작업과 무관한 코드 변경
- Git force 작업
- 사용자 요청 없는 commit/push
- Pose fine-tuning
- Tracking/ROS2/Dashboard/INT8 등 범위 밖 구현

---

# 20. 최종 보고 템플릿

`AGENTS.md`에 아래 형식을 그대로 사용할 수 있게 넣어라.

```markdown
## 작업 요약

### 환경
- Repository root:
- Branch:
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

# 21. Definition of Done

다음 완료 기준을 포함하라.

## 화요일

- requirements.md
- dataset_selection.md
- data_quality.md
- inspect_raw_data.py
- convert_to_yolo.py
- split_dataset.py
- validate_yolo_dataset.py
- visualize_labels.py
- dataset.yaml
- Detect smoke test 가능
- Pose smoke test 가능

## 수요일

- Detect baseline 결과
- threshold 초안
- Pose 검증
- Detect→Crop→Pose 초안

## 목요일

- rule classifier
- persistence
- JSONL
- event image
- visualization
- end-to-end 실행

## 금요일

- 통합 테스트
- README
- 최종 threshold
- Jira 종료
- 데모 실행 방법

## 전체 MVP

```text
입력 영상에서 person을 탐지하고,
탐지된 person에 pretrained Pose를 적용하며,
규칙 기반으로 normal 또는 possible_fallen을 판정하고,
약 1초 persistence 이후 JSONL과 이벤트 이미지를 저장할 수 있다.
```

---

# 22. AGENTS.md 작성 품질 기준

생성하는 문서는 다음을 만족해야 한다.

- 한국어로 작성
- 코드, 경로, 명령어, schema는 영어 유지
- 단순 요약본이 아닌 상세 운영 문서
- 최소 1,500줄을 억지로 채우지는 말되, 모든 규칙을 충분히 설명
- 불필요한 반복 금지
- 실제 저장소 분석 결과 반영
- 존재하지 않는 파일을 존재한다고 단정하지 않음
- 현재 파일과 다른 지시가 충돌하면 현재 저장소의 실제 구조를 우선
- 불확실한 부분은 `확인 필요` 또는 `현재 미확인`으로 표시
- Markdown heading 구조를 일관되게 사용
- 체크리스트와 표를 적절히 사용
- Codex가 이후 작업에서 바로 참조할 수 있을 정도로 구체적으로 작성

---

# 23. 파일 생성 지시

저장소 루트에 다음 파일을 생성하라.

```text
AGENTS.md
```

기존 `AGENTS.md`가 있다면 내용을 백업할 필요는 없지만, 삭제 전에 기존 유효 규칙이 누락되지 않는지 확인하고 병합하라.

파일 생성 후 다음을 수행하라.

```bash
git diff -- AGENTS.md
```

그리고 아래만 보고하라.

```text
1. 생성 또는 수정된 파일 경로
2. 문서 주요 목차
3. 실제 저장소에서 반영한 항목
4. 확인이 필요한 항목
5. AGENTS.md 줄 수
```

코드 구현은 아직 시작하지 않는다.

이번 작업은 **완성형 `AGENTS.md` 생성만 수행**한다.

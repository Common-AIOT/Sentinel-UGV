1) 탐지 클래스
Person
Fire Extinguisher
Exit
Danger Sign
2) MVP 파이프라인
Camera

↓

YOLO26 Detect

↓

Person인가?

↓

NO → 다음 프레임

YES

↓

Crop

↓

YOLO26 Pose

↓

Posture Rule

↓

normal
possible_fallen
unknown
3) 완료 조건

예를 들면

사람 탐지 성공

↓

Pose 실행 성공

↓

normal / possible_fallen 출력

↓

로그 저장
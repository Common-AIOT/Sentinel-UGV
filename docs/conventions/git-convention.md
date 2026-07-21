# Git Convention

Sentinel UGV의 브랜치, 커밋 메시지와 GitLab Merge Request 규칙입니다.

## 브랜치 전략

```text
develop → 작업 브랜치 → Merge Request → develop → 통합 테스트 → main
```

- `main`과 `develop`에는 직접 push하지 않습니다.
- 모든 작업은 Jira 이슈를 만든 후 최신 `develop`에서 시작합니다.
- 한 작업 브랜치에서는 하나의 Jira 이슈만 처리합니다.
- 병합은 CI, 리뷰와 충돌 해결을 마친 Merge Request로 수행합니다.
- Squash Merge 후 작업 브랜치를 삭제합니다.

## 브랜치 이름

```text
<type>/<scope>/<jira-key>-<description>
```

예: `feat/ros2/S15P11A301-145-frontier-exploration`

- type: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `chore`, `ci`, `hotfix`, `revert`
- scope: `frontend`, `backend`, `jetson`, `ros2`, `ai`, `camera`, `slam`, `navigation`, `control`, `streaming`, `database`, `common`, `deploy`, `docs`, `root`
- 소문자 영문을 사용하고 단어는 하이픈으로 구분합니다.

## 커밋과 MR 제목

```text
<type>(<scope>): <jira-key> <한국어 설명>
```

예: `fix(control): S15P11A301-162 명령 타임아웃 시 모터 정지`

- 하나의 커밋에는 하나의 목적만 포함합니다.
- `수정`, `작업`, `업데이트` 같은 모호한 표현과 끝의 마침표를 사용하지 않습니다.
- 임시 커밋은 허용하지만 병합할 때 정상 메시지 하나로 squash합니다.

## Merge Request 조건

- 대상 브랜치는 일반 작업의 경우 `develop`, 통합 버전은 `main`입니다.
- Jira 이슈, 변경 이유, 테스트 방법, 하드웨어 영향, 롤백 방법을 작성합니다.
- 최소 한 명의 리뷰와 성공한 CI가 필요합니다.
- 모터·E-Stop·전원 변경은 임베디드 담당 리뷰와 실제 장치 테스트 결과가 필수입니다.
- 공용 브랜치에 force push하지 않습니다.

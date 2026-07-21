# Testing

단위·컴포넌트·벤치·시뮬레이션·통합·장애 주입·성능·시연 리허설 결과를 보관합니다.

테스트 결과에는 다음 항목을 포함합니다.

- 대상 commit과 하드웨어/펌웨어 버전
- 환경과 재현 명령
- 기대 결과와 실제 결과
- 로그·그래프·영상의 저장 위치
- 실패 원인, 안전 영향과 후속 이슈

모터 테스트는 차량을 바닥에서 띄운 상태로 시작하고 물리 E-Stop을 먼저 확인합니다.

## GitLab 최소 CI

Merge Request와 기본 브랜치에서는 다음 검사를 필수로 실행합니다.

- `verify:runner`: Runner가 파이프라인과 저장소를 정상적으로 수신했는지 확인
- `validate:repository-structure`: 필수 모노레포 경로와 핵심 파일 확인
- `lint:shell`: `scripts/`의 셸 스크립트 정적 검사

`diagnose:runner`는 Runner 환경을 확인할 때만 수동으로 실행하며 Merge Request를 차단하지 않습니다. 파이프라인은 컨테이너 이미지를 실행할 수 있는 Docker 또는 Kubernetes executor를 전제로 합니다.

### 프로젝트 설정 확인

GitLab 프로젝트의 `Settings > CI/CD > Runners`에서 활성 Runner가 프로젝트에 할당되어 있고 paused 상태가 아닌지 확인합니다. Runner에 태그가 설정되어 있다면 `.gitlab-ci.yml`의 작업에도 동일한 태그를 추가해야 합니다.

`Settings > Merge requests > Merge checks`에서 파이프라인 성공 및 모든 discussion 해결을 Merge 조건으로 활성화합니다.

Merge Request를 만든 뒤 `verify:runner`, `validate:repository-structure`, `lint:shell` 세 작업이 모두 성공하면 초기 검증이 완료됩니다.

# 통합 명세서

[Sentinel UGV 최종 통합 명세서 v1.0-rc1](Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md)이 프로젝트 문서의 기준선입니다.

## 문서 관리 원칙

- 전체본의 버전, 결정 상태와 변경 이력을 먼저 갱신합니다.
- 영역별 장 문서는 전체본에서 생성하며 직접 수정하지 않습니다.
- API와 이벤트의 기계 검증 가능한 계약은 [`common/`](../../common/README.md)을 단일 기준점으로 사용합니다.
- 실제 부품 정격, 배선과 튜닝값은 검증 전까지 `TBD` 상태를 유지합니다.

## 분할 및 검증

저장소 루트에서 다음 명령을 실행합니다.

```powershell
./scripts/docs/split-integrated-spec.ps1
./scripts/docs/split-integrated-spec.ps1 -Check
```

스크립트는 1~38장, 부록 A~L과 참고 자료를 각 영역 폴더에 배치합니다. 부록은 [appendices/](appendices/)에 모아 둡니다.

# 운영 데이터 정비 런북 (S15P11A301-275)

과거 실주행 telemetry 를 임무에 소급 연결해 이력 화면의 거리·경로를 복원하고,
발표 전에 테스트 잔재를 지운다. **모든 명령은 EC2 에서 실행한다.**

젯슨이 !114(7/31) 이전에 telemetry 봉투에 missionId 를 넣지 않아 실주행 기록이
mission_id NULL 로 쌓였다. 임무 시간창으로 주인을 찾는다 — 로봇이 1대라 시간만으로
유일하게 결정된다.

## 0. 백업 (필수 — 유일한 롤백 수단)

```bash
docker exec postgres-prod pg_dump -U sentinel -d sentinel -Fc \
  > ~/backup/sentinel-$(date +%Y%m%d-%H%M).dump
```

컨테이너·계정·DB 이름은 `~/deploy/.env` 기준으로 맞춘다.
롤백: `docker exec -i postgres-prod pg_restore -U sentinel -d sentinel --clean < <백업파일>`

## 1. 사전 검증

```bash
docker exec -i postgres-prod psql -U sentinel -d sentinel < verify_backfill.sql
```

- **0번(시간창 겹침)이 0건인지 반드시 확인** — 겹치면 소급 매칭이 유일하지 않으므로 중단.
- 1·3번 수치를 기록해 둔다(사후 비교).
- **진행 중 임무가 없는 시점에 실행한다** — 재집계가 끝난 임무만 다루지만
  운영 중 UPDATE 경합을 피한다.

## 2. 소급 연결 + 재집계

```bash
docker exec -i postgres-prod psql -U sentinel -d sentinel < backfill_telemetry.sql
```

## 3. 사후 검증

`verify_backfill.sql` 재실행:

- 2번(시간창 안 orphan) = **0**
- 3번(거리 없는 완료 임무) 감소
- 4번 목록을 관제 임무 이력 화면과 대조 — 과거 임무에 거리·경로가 살아났는지

주의: 1번(전체 orphan)은 0이 되지 않는다 — 임무 밖 구간의 NULL 은 정상(31-5).
started_at·ended_at 이 없는 임무는 복원 대상이 아니다(시간창을 모른다).

## 4. 테스트 데이터 청소

1. `cleanup_test_data.sql` 상단의 후보 조회를 실행해 목록을 뽑는다.
2. 삭제할 임무 id 를 **사람이 확정**한다 (실주행 임무 오삭제 주의 — 젯슨 검증
   임무·무의미한 수 초짜리 임무가 대상).
3. 파일의 주석 블록에 id 를 채워 실행한다. placeholder 썸네일 2건(#266 검증 잔재,
   id 고정)은 그대로 주석만 풀면 된다.

## 5. S3(MinIO) 객체 청소

SQL 은 DB 행만 지운다. 객체는 mc 로:

```bash
# 버킷 테스트 파일 (가짜 mission id 경로)
mc rm --recursive --force local/sentinel-ugv-assets/missions/12121212
mc rm --recursive --force local/sentinel-ugv-assets/missions/13131313
mc rm --recursive --force local/sentinel-ugv-assets/missions/14141414

# 삭제 확정한 임무들의 객체
mc rm --recursive --force local/sentinel-ugv-assets/missions/<mission-id>

# placeholder 썸네일 2건
mc rm "local/sentinel-ugv-assets/missions/4bde8ad1-c74b-4d42-bec3-9f71af94b41a/encounters/a2f6a0e0-be75-4d83-b4fd-4a13ea85973d/thumbnail.jpg"
mc rm "local/sentinel-ugv-assets/missions/4bde8ad1-c74b-4d42-bec3-9f71af94b41a/encounters/ee3dca1b-79b6-4ddb-bafe-acd6c02679cc/thumbnail.jpg"
```

`local` 은 EC2 에 설정된 mc alias 이름으로 바꾼다 (`mc alias list` 로 확인).

## 6. 마무리 확인

- 관제 임무 이력: 목록에 테스트 잔재 없음, 과거 임무 거리·경로 표시
- `GET /api/v1/missions` 응답의 durationSec·distanceM 채워짐

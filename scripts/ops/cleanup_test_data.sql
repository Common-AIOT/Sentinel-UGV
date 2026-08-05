-- S15P11A301-275: 운영 테스트 데이터 청소.
--
-- ⚠️ 바로 실행하는 파일이 아니다. 먼저 아래 "후보 조회"로 목록을 뽑아 삭제할
-- 임무를 사람이 확정한 뒤, 확정된 id 만 del_missions 에 채워 실행한다.
-- 실기체 실주행 임무를 지우면 복구는 백업뿐이다.

-- ── 후보 조회 — 삭제 판단용 목록 (읽기 전용) ─────────────────────────────
SELECT m.id, m.created_at, m.status, m.end_reason,
       r.duration_sec, round(r.distance_m::numeric, 1) AS distance_m,
       (SELECT count(*) FROM encounters e WHERE e.mission_id = m.id)   AS encounters,
       (SELECT count(*) FROM media_assets a WHERE a.mission_id = m.id) AS media
FROM missions m
LEFT JOIN mission_results r ON r.mission_id = m.id
ORDER BY m.created_at;

-- ── 임무 삭제 (id 확정 후 주석 해제·채워서 실행) ─────────────────────────
-- missions 의 FK CASCADE 가 encounters·media_assets·mission_results·events·
-- control_commands 를 함께 지운다. hypertable 은 FK 가 없어 직접 지운다.
-- S3 객체는 SQL 로 지워지지 않는다 — runbook 의 mc 명령으로 별도 삭제.
--
-- BEGIN;
-- CREATE TEMP TABLE del_missions(id uuid) ON COMMIT DROP;
-- INSERT INTO del_missions VALUES
--     ('<삭제할-mission-id>'),
--     ('<삭제할-mission-id>');
-- DELETE FROM robot_pose          WHERE mission_id IN (SELECT id FROM del_missions);
-- DELETE FROM robot_metrics       WHERE mission_id IN (SELECT id FROM del_missions);
-- DELETE FROM environment_metrics WHERE mission_id IN (SELECT id FROM del_missions);
-- DELETE FROM network_metrics     WHERE mission_id IN (SELECT id FROM del_missions);
-- DELETE FROM safety_events       WHERE mission_id IN (SELECT id FROM del_missions);
-- DELETE FROM missions            WHERE id IN (SELECT id FROM del_missions);
-- COMMIT;

-- ── #266 운영 검증이 남긴 placeholder 썸네일 2건 (id 확정, 주석 해제로 실행) ──
-- 내용물이 실제 썸네일이 아니라 160바이트 테스트 이미지다. 행을 지우면 해당
-- 발견은 "썸네일 없음"으로 돌아간다(영상·음성은 무관). S3 객체는 mc 로 삭제.
--
-- DELETE FROM media_assets WHERE id IN (
--     '4c2e6f8c-9386-4432-a23d-d256e2ac1f5a',  -- encounter a2f6a0e0 썸네일
--     'cb6494a7-72d5-4c87-a110-2d033022cfb0'   -- encounter ee3dca1b 썸네일
-- );

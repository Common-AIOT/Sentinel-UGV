-- S15P11A301-275: 임무에 안 묶인 telemetry 소급 연결 + 결과 재집계
--
-- 배경: 젯슨이 !114(7/31) 이전에는 telemetry 봉투에 missionId 를 넣지 않아,
-- 실주행 위치·지표가 mission_id NULL 로 쌓였다. 그래서 과거 임무의 이동 거리와
-- 지도 경로가 비어 있다. 임무 시간창(started_at~ended_at)으로 주인을 찾아준다.
--
-- 전제:
--   1. 로봇이 1대뿐이라(robots 1행) 시간창만으로 임무가 유일하게 결정된다.
--      hypertable 에는 robot_id 컬럼이 없으므로 이 전제가 깨지면 이 방식도 깨진다.
--   2. 실행 전 verify_backfill.sql 의 "임무 시간창 겹침" 검사가 0건이어야 한다.
--   3. 실행 전 pg_dump 백업 (runbook 참조).
--
-- 임무 밖 구간(대기 중 telemetry)의 NULL 은 정상이므로(명세 31-5) 남는다.
-- 멱등: 다시 실행해도 이미 연결된 행(mission_id NOT NULL)은 건드리지 않는다.

BEGIN;

-- ── 1. 시간창 소급 연결 (hypertable 5종) ────────────────────────────────
UPDATE robot_pose t SET mission_id = m.id
FROM missions m
WHERE t.mission_id IS NULL
  AND m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
  AND t.time >= m.started_at AND t.time <= m.ended_at;

UPDATE robot_metrics t SET mission_id = m.id
FROM missions m
WHERE t.mission_id IS NULL
  AND m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
  AND t.time >= m.started_at AND t.time <= m.ended_at;

UPDATE environment_metrics t SET mission_id = m.id
FROM missions m
WHERE t.mission_id IS NULL
  AND m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
  AND t.time >= m.started_at AND t.time <= m.ended_at;

UPDATE network_metrics t SET mission_id = m.id
FROM missions m
WHERE t.mission_id IS NULL
  AND m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
  AND t.time >= m.started_at AND t.time <= m.ended_at;

UPDATE safety_events t SET mission_id = m.id
FROM missions m
WHERE t.mission_id IS NULL
  AND m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
  AND t.time >= m.started_at AND t.time <= m.ended_at;

-- ── 2. 결과 재집계 — CommandAckWriter(#166)의 INSERT_RESULTS 와 같은 공식 ──
-- 기존 행도 갱신한다: 소급 전에 집계돼 distance_m 이 NULL 로 굳은 임무를 살린다.
-- 공식이 결정적이라 정상 행을 다시 계산해도 같은 값이다(멱등).
INSERT INTO mission_results (mission_id, duration_sec, distance_m, coverage, detection_count)
SELECT m.id,
       CAST(EXTRACT(EPOCH FROM (m.ended_at - m.started_at)) AS INTEGER),
       (SELECT sum(step) FROM (
            SELECT sqrt(power(x - lag(x) OVER (ORDER BY time), 2)
                      + power(y - lag(y) OVER (ORDER BY time), 2)) AS step
            -- 좌표 없는 엔코더 전용 행(V5)이 끼면 인접 거리가 끊긴다.
            FROM robot_pose WHERE mission_id = m.id AND x IS NOT NULL
        ) steps),
       NULL,
       (SELECT count(*) FROM encounters WHERE mission_id = m.id)
FROM missions m
WHERE m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
ON CONFLICT (mission_id) DO UPDATE
    SET duration_sec    = EXCLUDED.duration_sec,
        distance_m      = EXCLUDED.distance_m,
        detection_count = EXCLUDED.detection_count;

COMMIT;

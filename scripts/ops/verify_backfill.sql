-- S15P11A301-275: 소급 연결 전/후 검증. backfill 전에 한 번, 후에 한 번 실행해 비교한다.

-- ── 0. 임무 시간창 겹침 검사 — 반드시 0건이어야 소급이 유일하게 매칭된다 ──
SELECT a.id AS mission_a, b.id AS mission_b, a.started_at, a.ended_at, b.started_at, b.ended_at
FROM missions a
JOIN missions b ON a.id < b.id
WHERE a.started_at IS NOT NULL AND a.ended_at IS NOT NULL
  AND b.started_at IS NOT NULL AND b.ended_at IS NOT NULL
  AND a.started_at <= b.ended_at AND b.started_at <= a.ended_at;

-- ── 1. 주인 없는 행 수 (전/후 비교. 임무 밖 구간은 NULL 이 정상이라 0 이 되지 않는다) ──
SELECT 'robot_pose' AS t, count(*) AS orphan FROM robot_pose WHERE mission_id IS NULL
UNION ALL SELECT 'robot_metrics', count(*) FROM robot_metrics WHERE mission_id IS NULL
UNION ALL SELECT 'environment_metrics', count(*) FROM environment_metrics WHERE mission_id IS NULL
UNION ALL SELECT 'network_metrics', count(*) FROM network_metrics WHERE mission_id IS NULL
UNION ALL SELECT 'safety_events', count(*) FROM safety_events WHERE mission_id IS NULL;

-- ── 2. 임무 시간창 안에 있는 주인 없는 행 수 — backfill 후 0 이어야 한다 ──
SELECT count(*) AS orphan_in_window
FROM robot_pose t
JOIN missions m ON m.started_at IS NOT NULL AND m.ended_at IS NOT NULL
             AND t.time >= m.started_at AND t.time <= m.ended_at
WHERE t.mission_id IS NULL;

-- ── 3. 끝난 임무 중 거리 없는 것 (전/후 비교 — 감소해야 한다) ──
SELECT count(*) AS ended_without_distance
FROM missions m
LEFT JOIN mission_results r ON r.mission_id = m.id
WHERE m.ended_at IS NOT NULL
  AND (r.mission_id IS NULL OR r.distance_m IS NULL);

-- ── 4. 임무별 결과 한눈에 (후 실행 — 화면과 대조용) ──
SELECT m.id, m.created_at, m.status, r.duration_sec, round(r.distance_m::numeric, 1) AS distance_m,
       r.detection_count,
       (SELECT count(*) FROM robot_pose p WHERE p.mission_id = m.id) AS pose_rows
FROM missions m
LEFT JOIN mission_results r ON r.mission_id = m.id
ORDER BY m.created_at;

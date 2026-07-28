-- 젯슨은 임무 외 상태에서도 telemetry 를 발행하며 그때 missionId 는 null 이다
-- (common/schemas/envelope.schema.json, 명세 31-5).
-- NOT NULL 을 유지하면 임무 시작 전 telemetry 를 한 건도 저장할 수 없으므로 제약을 푼다.
-- 임무별 조회는 기존 (mission_id, time DESC) 인덱스를 그대로 사용한다.

ALTER TABLE robot_pose ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE robot_metrics ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE environment_metrics ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE network_metrics ALTER COLUMN mission_id DROP NOT NULL;
ALTER TABLE safety_events ALTER COLUMN mission_id DROP NOT NULL;

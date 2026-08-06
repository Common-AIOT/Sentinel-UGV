-- S15P11A301-301: 음성 상호작용 당시 로봇 위치와 추가 요구조자 미확인 제보.
-- 전체 원문은 events.payload_json에도 보존하지만 encounter 상세 API에서 즉시 조회할 수
-- 있도록 최신 보고의 두 필드를 encounter에 투영한다.
ALTER TABLE encounters
    ADD COLUMN voice_encounter_pose JSONB,
    ADD COLUMN additional_person_reports JSONB NOT NULL DEFAULT '[]'::jsonb;

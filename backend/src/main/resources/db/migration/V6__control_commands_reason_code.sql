-- 명령 거부·실패 사유 저장 (S15P11A301-207).
--
-- ACK 의 reasonCode(ROBOT_BUSY·NOT_IMPLEMENTED 등)가 로그에만 남고 버려져서,
-- 관제가 "거부됨"까지만 알고 이유를 보여줄 수 없었다. 조회 API 와 함께 노출한다.
ALTER TABLE control_commands ADD COLUMN IF NOT EXISTS reason_code TEXT;

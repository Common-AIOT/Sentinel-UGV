-- 녹화기 상태 적재 (S15P11A301-310).
--
-- 젯슨이 telemetry health 로 보내던 두 필드를 서버가 버리고 있었다(S15P11A301-309 발신 완료).
-- 마감이 실패한 이벤트는 event.mp4 가 없어 업로드 경로를 아예 타지 않는데, encounter 는
-- MQTT 로 따로 적재되므로 **서버에는 미디어 없는 발견만 남고 사유가 없었다.** 관제는 그것을
-- 「아직 업로드 전」과 구별할 수 없다 — S15P11A301-304 의 PTS 결함이 19건 쌓일 때까지
-- 아무도 알아채지 못한 이유가 이것이다.
--
-- 두 컬럼을 따로 두는 것이 핵심이다. `recorder_ok = true` 와 `recorder_last_failure` 가
-- 함께 있는 것이 정상 조합이며 「지금은 정상이지만 이번 기동에 실패가 있었다」는 뜻이다.
-- 하나로 합치면 성공 한 번에 실패 기록이 덮여 간헐 실패를 다시 놓친다.
ALTER TABLE robot_metrics ADD COLUMN IF NOT EXISTS recorder_ok BOOLEAN;

-- 사유는 TEXT 다. 젯슨이 `RECORDING_FAILED_{사유}` 로 만들어 값이 늘어나므로 열거형으로
-- 고정하지 않는다. 현재 관측되는 값: RECORDING_FAILED_PTS_REGRESSION,
-- RECORDING_FAILED_NO_SEGMENTS, RECORDING_FAILED_DISK_FULL, RECORDING_FAILED_UNEXPECTED,
-- CORRUPT. (hypertable 문자열 컬럼은 TimescaleDB 권장에 따라 TEXT 를 쓴다.)
ALTER TABLE robot_metrics ADD COLUMN IF NOT EXISTS recorder_last_failure TEXT;

-- 31-7 업로드 계약 정합 (S15P11A301-132).
-- 완료 API 가 스토리지 실물과 비교할 크기와, 무결성 검증용 체크섬을 저장한다.
-- sha256 은 저장만 하고 완료 검증은 HeadObject 존재·크기 비교로 한다.

ALTER TABLE media_assets
    ADD COLUMN sha256     VARCHAR(64),
    ADD COLUMN size_bytes BIGINT;

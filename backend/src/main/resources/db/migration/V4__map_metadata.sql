-- 지도 메타데이터 (S15P11A301-197). 젯슨이 완료 호출 본문에 실어 보내는 전정밀 값.
-- yaml 의 origin 은 유효숫자 3자리로 잘리므로(실측 최대 2.65cm 오차) 본문 값이 권위이고,
-- 본문이 없으면 스토리지의 yaml 을 파싱해 채운다(폴백). 전부 nullable — 등록 직후나
-- 구버전 젯슨의 지도는 값이 없을 수 있다.

ALTER TABLE maps
    ADD COLUMN resolution  DOUBLE PRECISION,
    ADD COLUMN origin_x    DOUBLE PRECISION,
    ADD COLUMN origin_y    DOUBLE PRECISION,
    ADD COLUMN origin_yaw  DOUBLE PRECISION,
    ADD COLUMN width       INTEGER,
    ADD COLUMN height      INTEGER,
    ADD COLUMN pgm_sha256  VARCHAR(64),
    ADD COLUMN yaml_sha256 VARCHAR(64);

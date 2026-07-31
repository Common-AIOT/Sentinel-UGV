package com.sentinel.backend.media;

import java.time.Duration;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.config.S3Properties;
import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.media.dto.MapCompleteResponse;
import com.sentinel.backend.media.dto.MapUploadResponse;

import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;

/**
 * SLAM 지도 업로드 계약 (S15P11A301-185, #171 선행).
 *
 * <p>한 지도 = pgm+yaml 두 객체 = {@code maps} 행 하나(13.2). 미디어(31-7)와 같은
 * 발급→PUT→완료 2단계이며, object key 는 29.6 스타일로 서버가 결정한다.
 *
 * <p>발급은 멱등이다: 임무당 지도 1개(MVP)로 보고, 이미 행이 있으면 같은 mapId 로
 * 새 URL 을 발급한다. 미디어(#154)와 달리 멱등 반환이 안전한 이유는 젯슨이 응답의
 * mapId 를 읽어 공식 식별자로 쓰기 때문이다. maps(mission_id) 에 UNIQUE 가 없어
 * 동시 발급이면 행이 중복될 수 있으나, 클라이언트가 젯슨 한 대뿐이라 감수한다.
 */
@Service
public class MapUploadService {

    private static final String CONTENT_TYPE = "application/octet-stream";

    private final S3Presigner presigner;
    private final S3Client s3Client;
    private final S3Properties props;
    private final JdbcTemplate jdbc;

    public MapUploadService(S3Presigner presigner, S3Client s3Client, S3Properties props, JdbcTemplate jdbc) {
        this.presigner = presigner;
        this.s3Client = s3Client;
        this.props = props;
        this.jdbc = jdbc;
    }

    /**
     * maps 행을 확보하고 pgm·yaml 각각의 Presigned PUT URL 을 발급한다.
     * key 가 mapId 에서 파생돼 결정적이므로 발급 시점에 행을 통째로 만든다 —
     * 젯슨이 임무 초반에 호출해 임무 내내 mapId 를 쓸 수 있다.
     */
    public MapUploadResponse createUpload(UUID missionId, Duration ttl) {
        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }

        List<UUID> existing = jdbc.query(
                "SELECT id FROM maps WHERE mission_id = ? ORDER BY created_at LIMIT 1",
                (rs, i) -> rs.getObject("id", UUID.class), missionId);

        UUID mapId;
        if (existing.isEmpty()) {
            mapId = UUID.randomUUID();
            jdbc.update("INSERT INTO maps (id, mission_id, s3_key_pgm, s3_key_yaml) VALUES (?, ?, ?, ?)",
                    mapId, missionId, pgmKey(missionId, mapId), yamlKey(missionId, mapId));
        } else {
            mapId = existing.getFirst();
        }

        String pgmKey = pgmKey(missionId, mapId);
        String yamlKey = yamlKey(missionId, mapId);
        return new MapUploadResponse(mapId, pgmKey, yamlKey,
                presignPut(pgmKey, ttl), presignPut(yamlKey, ttl),
                CONTENT_TYPE, ttl.toSeconds());
    }

    /** 두 객체가 스토리지에 실재하는지 확인한다. 재호출에도 같은 응답(멱등). */
    public MapCompleteResponse completeUpload(UUID mapId) {
        List<String[]> rows = jdbc.query(
                "SELECT s3_key_pgm, s3_key_yaml FROM maps WHERE id = ?",
                (rs, i) -> new String[]{rs.getString("s3_key_pgm"), rs.getString("s3_key_yaml")}, mapId);
        if (rows.isEmpty()) {
            throw new BusinessException(ErrorCode.MAP_NOT_FOUND);
        }
        verifyExists(rows.getFirst()[0]);
        verifyExists(rows.getFirst()[1]);
        return new MapCompleteResponse(mapId);
    }

    /** 29.6 스타일 결정적 key. 재시도에도 같은 위치에 덮어쓴다. */
    private String pgmKey(UUID missionId, UUID mapId) {
        return "missions/%s/maps/%s/map.pgm".formatted(missionId, mapId);
    }

    private String yamlKey(UUID missionId, UUID mapId) {
        return "missions/%s/maps/%s/map.yaml".formatted(missionId, mapId);
    }

    private void verifyExists(String objectKey) {
        try {
            s3Client.headObject(b -> b.bucket(props.bucket()).key(objectKey));
        } catch (NoSuchKeyException e) {
            throw new BusinessException(ErrorCode.MAP_UPLOAD_INCOMPLETE,
                    "스토리지에 객체가 없습니다: " + objectKey);
        }
    }

    private String presignPut(String objectKey, Duration ttl) {
        PutObjectRequest put = PutObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey)
                .contentType(CONTENT_TYPE)
                .build();
        PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
                .signatureDuration(ttl)
                .putObjectRequest(put)
                .build();
        return presigner.presignPutObject(presignRequest).url().toString();
    }
}

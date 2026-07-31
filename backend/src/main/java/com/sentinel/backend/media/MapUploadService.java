package com.sentinel.backend.media;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.regex.Matcher;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.config.S3Properties;
import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.media.dto.MapCompleteRequest;
import com.sentinel.backend.media.dto.MapCompleteResponse;
import com.sentinel.backend.media.dto.MapUploadResponse;
import com.sentinel.backend.media.dto.MapViewResponse;

import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
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

    private static final Logger log = LoggerFactory.getLogger(MapUploadService.class);

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
     * key 가 mapId 에서 파생돼 결정적이므로 발급 시점에 행을 통째로 만든다.
     *
     * <p>clientMapId 는 젯슨이 SLAM 세션 시작 때 만든 식별자다(S15P11A301-189).
     * 망 단절 중에도 젯슨이 임무 내내 같은 mapId 를 telemetry·encounter 에 실을 수
     * 있어야 하므로, 식별자 생성을 서버에 의존시키지 않는다. 임무에 기존 행이
     * 있으면 기존 행이 이긴다 — 응답의 mapId 가 권위라는 계약은 그대로다.
     */
    public MapUploadResponse createUpload(UUID missionId, UUID clientMapId, Duration ttl) {
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
            mapId = clientMapId != null ? clientMapId : UUID.randomUUID();
            try {
                jdbc.update("INSERT INTO maps (id, mission_id, s3_key_pgm, s3_key_yaml) VALUES (?, ?, ?, ?)",
                        mapId, missionId, pgmKey(missionId, mapId), yamlKey(missionId, mapId));
            } catch (DuplicateKeyException e) {
                // 다른 임무가 이미 쓰는 mapId(PK 충돌) — 젯슨의 재사용 버그를 500 무한
                // 재시도 대신 409 영구 실패로 굳힌다(#154 원칙).
                throw new BusinessException(ErrorCode.MAP_ID_CONFLICT,
                        "이미 다른 임무에 등록된 mapId 입니다: " + mapId);
            }
        } else {
            mapId = existing.getFirst();
        }

        String pgmKey = pgmKey(missionId, mapId);
        String yamlKey = yamlKey(missionId, mapId);
        return new MapUploadResponse(mapId, pgmKey, yamlKey,
                presignPut(pgmKey, ttl), presignPut(yamlKey, ttl),
                CONTENT_TYPE, ttl.toSeconds());
    }

    /**
     * 두 객체의 실재를 확인하고 메타데이터를 저장한다 (S15P11A301-197).
     *
     * <p>값의 우선순위는 본문 > 기존 저장값 > yaml 폴백이다 — 본문의 origin 은 젯슨이
     * live 격자에서 읽은 전정밀 값이라 yaml(유효숫자 3자리 절단)보다 정확하다.
     * 재호출하면 새 본문 값으로 갱신되므로, 과거에 본문 없이 완료된 지도도
     * 완료 재호출 한 번으로 정밀값을 채울 수 있다.
     */
    public MapCompleteResponse completeUpload(UUID mapId, MapCompleteRequest body) {
        List<MapRow> rows = jdbc.query(
                "SELECT id, s3_key_pgm, s3_key_yaml, " + META_COLUMNS + " FROM maps WHERE id = ?",
                this::mapRow, mapId);
        if (rows.isEmpty()) {
            throw new BusinessException(ErrorCode.MAP_NOT_FOUND);
        }
        MapRow row = rows.getFirst();
        verifyExists(row.pgmKey());
        verifyExists(row.yamlKey());

        Double resolution = coalesce(body == null ? null : body.resolution(), row.resolution());
        Double originX = coalesce(body == null ? null : body.originX(), row.originX());
        Double originY = coalesce(body == null ? null : body.originY(), row.originY());
        Double originYaw = coalesce(body == null ? null : body.originYaw(), row.originYaw());
        Integer width = coalesce(body == null ? null : body.width(), row.width());
        Integer height = coalesce(body == null ? null : body.height(), row.height());
        String pgmSha = coalesce(body == null ? null : body.pgmSha256(), row.pgmSha256());
        String yamlSha = coalesce(body == null ? null : body.yamlSha256(), row.yamlSha256());

        // 본문·기존값으로 못 채운 좌표계 정보는 스토리지의 yaml 에서 읽는다(절단값이지만 없는 것보단 낫다).
        if (resolution == null || originX == null || originY == null || originYaw == null) {
            YamlMeta yaml = parseYamlQuietly(row.yamlKey());
            if (yaml != null) {
                resolution = coalesce(resolution, yaml.resolution());
                originX = coalesce(originX, yaml.originX());
                originY = coalesce(originY, yaml.originY());
                originYaw = coalesce(originYaw, yaml.originYaw());
            }
        }

        jdbc.update("""
                UPDATE maps SET resolution = ?, origin_x = ?, origin_y = ?, origin_yaw = ?,
                       width = ?, height = ?, pgm_sha256 = ?, yaml_sha256 = ? WHERE id = ?
                """,
                resolution, originX, originY, originYaw, width, height, pgmSha, yamlSha, mapId);
        return new MapCompleteResponse(mapId);
    }

    /**
     * 임무의 지도 조회용 Presigned GET URL 쌍 (S15P11A301-187). 관제는 missionId 만
     * 알고 시작하므로 임무 기준으로 조회한다. media view-url 과 같은 정책으로
     * 객체 실재는 검증하지 않는다 — 완료 전 지도면 다운로드가 404 로 실패할 뿐이다.
     */
    public MapViewResponse createViewUrls(UUID missionId, Duration ttl) {
        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }
        List<MapRow> rows = jdbc.query(
                "SELECT id, s3_key_pgm, s3_key_yaml, " + META_COLUMNS
                        + " FROM maps WHERE mission_id = ? ORDER BY created_at LIMIT 1",
                this::mapRow, missionId);
        if (rows.isEmpty()) {
            throw new BusinessException(ErrorCode.MAP_NOT_FOUND);
        }
        MapRow map = rows.getFirst();
        return new MapViewResponse(map.id(),
                presignGet(map.pgmKey(), ttl), presignGet(map.yamlKey(), ttl), ttl.toSeconds(),
                map.resolution(), map.originX(), map.originY(), map.originYaw(),
                map.width(), map.height());
    }

    private static final String META_COLUMNS =
            "resolution, origin_x, origin_y, origin_yaw, width, height, pgm_sha256, yaml_sha256";

    private MapRow mapRow(java.sql.ResultSet rs, int i) throws java.sql.SQLException {
        return new MapRow(
                rs.getObject("id", UUID.class),
                rs.getString("s3_key_pgm"), rs.getString("s3_key_yaml"),
                rs.getObject("resolution", Double.class),
                rs.getObject("origin_x", Double.class),
                rs.getObject("origin_y", Double.class),
                rs.getObject("origin_yaw", Double.class),
                rs.getObject("width", Integer.class),
                rs.getObject("height", Integer.class),
                rs.getString("pgm_sha256"), rs.getString("yaml_sha256"));
    }

    private record MapRow(UUID id, String pgmKey, String yamlKey,
                          Double resolution, Double originX, Double originY, Double originYaw,
                          Integer width, Integer height, String pgmSha256, String yamlSha256) {
    }

    private record YamlMeta(Double resolution, Double originX, Double originY, Double originYaw) {
    }

    private static <T> T coalesce(T preferred, T fallback) {
        return preferred != null ? preferred : fallback;
    }

    /**
     * 스토리지의 yaml(~130B)에서 resolution·origin 을 읽는다. 실패해도 완료를 막지
     * 않는다 — 메타데이터는 보강 정보이고 완료 검증의 본체는 객체 실재 확인이다.
     */
    private YamlMeta parseYamlQuietly(String yamlKey) {
        try {
            String text = s3Client.getObjectAsBytes(
                    b -> b.bucket(props.bucket()).key(yamlKey)).asUtf8String();
            Double resolution = null, ox = null, oy = null, oyaw = null;
            Matcher r = YAML_RESOLUTION.matcher(text);
            if (r.find()) {
                resolution = Double.parseDouble(r.group(1));
            }
            Matcher o = YAML_ORIGIN.matcher(text);
            if (o.find()) {
                String[] parts = o.group(1).split(",");
                if (parts.length >= 3) {
                    ox = Double.parseDouble(parts[0].trim());
                    oy = Double.parseDouble(parts[1].trim());
                    oyaw = Double.parseDouble(parts[2].trim());
                }
            }
            return new YamlMeta(resolution, ox, oy, oyaw);
        } catch (Exception e) {
            log.warn("yaml 메타데이터 파싱 실패({}): {}", yamlKey, e.getMessage());
            return null;
        }
    }

    private static final java.util.regex.Pattern YAML_RESOLUTION =
            java.util.regex.Pattern.compile("resolution:\\s*([-0-9.eE]+)");
    private static final java.util.regex.Pattern YAML_ORIGIN =
            java.util.regex.Pattern.compile("origin:\\s*\\[([^\\]]+)]");

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

    private String presignGet(String objectKey, Duration ttl) {
        GetObjectRequest get = GetObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey)
                .build();
        GetObjectPresignRequest presignRequest = GetObjectPresignRequest.builder()
                .signatureDuration(ttl)
                .getObjectRequest(get)
                .build();
        return presigner.presignGetObject(presignRequest).url().toString();
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

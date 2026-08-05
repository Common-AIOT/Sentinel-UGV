package com.sentinel.backend.media;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import com.sentinel.backend.common.config.S3Properties;

import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;

/**
 * 오래 PENDING 인 미디어를 스토리지 실물과 대조해 정리한다 (13.6 「업로드 중·유실 구분」).
 *
 * <p>젯슨이 스토리지에 직접 올리고 서버는 완료 통지(31-7 5단계)로만 상태를 알기 때문에,
 * 통지가 유실되면 행은 영원히 PENDING 이고 화면에서는 "업로드 중"과 "유실"이 구분되지
 * 않았다. 발급 후 {@link #STALE_AFTER} 가 지난 PENDING 행에 대해:
 *
 * <ul>
 *   <li>객체가 있고 크기·체크섬이 등록값과 맞으면 → AVAILABLE (완료 통지 유실 복구.
 *       29.4 "업로드 성공 여부가 불명확하면 같은 object key 를 조회"의 서버 쪽 대칭)
 *   <li>그 외(객체 없음·크기 다름·체크섬 다름) → FAILED (유실 판정)
 * </ul>
 *
 * <p>FAILED 는 서버 관점의 판정일 뿐 종착역이 아니다 — 젯슨 Outbox 는 지수 백오프로
 * 재시도하므로(29.4) 뒤늦은 complete 가 검증을 통과하면 AVAILABLE 로 복구된다. 재발급
 * 후 다시 올리는 중인 행을 유실로 잘못 찍어도 같은 경로로 스스로 낫는다.
 *
 * <p>기준 시각이 created_at(최초 발급)인 것은 의도다 — 재발급은 created_at 을 갱신하지
 * 않지만, 위 자가 복구 덕에 오판의 비용이 컬럼 추가보다 싸다.
 */
@Component
public class MediaReconciler {

    private static final Logger log = LoggerFactory.getLogger(MediaReconciler.class);

    /** 업로드 URL TTL(10분) + 여유. 이보다 오래 PENDING 이면 통지 유실이나 업로드 실패다. */
    private static final Duration STALE_AFTER = Duration.ofMinutes(15);

    private static final String FIND_STALE = """
            SELECT id, s3_key, sha256, size_bytes FROM media_assets
            WHERE storage_status = 'PENDING' AND created_at < ?
            """;

    private final JdbcTemplate jdbc;
    private final S3Client s3Client;
    private final S3Properties props;
    private final MediaService mediaService;

    public MediaReconciler(JdbcTemplate jdbc, S3Client s3Client, S3Properties props, MediaService mediaService) {
        this.jdbc = jdbc;
        this.s3Client = s3Client;
        this.props = props;
        this.mediaService = mediaService;
    }

    @Scheduled(initialDelay = 60_000, fixedDelay = 300_000)
    public void reconcile() {
        List<StaleAsset> stale = jdbc.query(FIND_STALE,
                (rs, i) -> new StaleAsset(
                        rs.getObject("id", UUID.class),
                        rs.getString("s3_key"),
                        rs.getString("sha256"),
                        rs.getObject("size_bytes", Long.class)),
                Timestamp.from(Instant.now().minus(STALE_AFTER)));
        for (StaleAsset asset : stale) {
            try {
                resolve(asset);
            } catch (Exception e) {
                // 한 건의 실패가 나머지 정리를 막지 않는다. 다음 주기에 다시 본다.
                log.warn("미디어 정리 건너뜀: {} ({})", asset.id(), e.toString());
            }
        }
    }

    private void resolve(StaleAsset asset) {
        HeadObjectResponse head;
        try {
            head = s3Client.headObject(b -> b.bucket(props.bucket()).key(asset.s3Key()));
        } catch (NoSuchKeyException e) {
            markFailed(asset, "객체 없음");
            return;
        }
        if (asset.sizeBytes() == null || head.contentLength() == null
                || !head.contentLength().equals(asset.sizeBytes())) {
            markFailed(asset, "크기 불일치 (등록 %s, 실물 %s)".formatted(asset.sizeBytes(), head.contentLength()));
            return;
        }
        if (asset.sha256() != null && !mediaService.hashObject(asset.s3Key()).equalsIgnoreCase(asset.sha256())) {
            markFailed(asset, "체크섬 불일치");
            return;
        }
        // complete 와의 경합을 storage_status 조건으로 막는다 — 어느 쪽이 이겨도 AVAILABLE.
        int updated = jdbc.update(
                "UPDATE media_assets SET storage_status = 'AVAILABLE' WHERE id = ? AND storage_status = 'PENDING'",
                asset.id());
        if (updated > 0) {
            log.info("완료 통지 유실 복구: {} → AVAILABLE ({})", asset.id(), asset.s3Key());
        }
    }

    private void markFailed(StaleAsset asset, String reason) {
        int updated = jdbc.update(
                "UPDATE media_assets SET storage_status = 'FAILED' WHERE id = ? AND storage_status = 'PENDING'",
                asset.id());
        if (updated > 0) {
            log.warn("미디어 유실 판정: {} → FAILED — {}", asset.id(), reason);
        }
    }

    private record StaleAsset(UUID id, String s3Key, String sha256, Long sizeBytes) {
    }
}

package com.sentinel.backend.media;

import java.io.IOException;
import java.sql.Timestamp;
import java.time.Duration;
import java.util.List;
import java.util.UUID;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.config.S3Properties;
import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.media.dto.MediaCompleteRequest;
import com.sentinel.backend.media.dto.MediaCompleteResponse;
import com.sentinel.backend.media.dto.PresignedUrlResponse;
import com.sentinel.backend.media.dto.UploadUrlRequest;
import com.sentinel.backend.media.dto.UploadUrlResponse;

import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.HeadObjectResponse;
import software.amazon.awssdk.services.s3.model.NoSuchKeyException;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;

/**
 * 미디어 업로드 계약 구현 (명세 31-7 「이벤트 영상 업로드」).
 *
 * <p>젯슨은 스토리지에 직접 올리므로(4단계) 서버는 발급(2단계)과 완료 통지(5단계)로만
 * 상태를 안다. object key 와 Content-Type 은 서버가 결정한다(31-11) — 클라이언트가
 * 키를 정하면 버킷의 임의 경로에 쓸 수 있다.
 *
 * <p>상태는 13.6 을 따르되 UPLOADING 전이는 생략한다 — PUT 이 원자적이라 서버가 관측할
 * 수 없는 상태다. 서버가 쓰는 상태는 PENDING·AVAILABLE·FAILED 세 가지이고, 오래된
 * PENDING 의 유실 판정은 {@link MediaReconciler} 가 맡는다.
 */
@Service
public class MediaService {

    private static final String UPSERT_ASSET = """
            INSERT INTO media_assets (id, mission_id, encounter_id, type, s3_key, storage_status, sha256, size_bytes)
            VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?)
            ON CONFLICT (id) DO UPDATE SET sha256 = EXCLUDED.sha256, size_bytes = EXCLUDED.size_bytes
            """;

    private static final String COMPLETE_ASSET = """
            UPDATE media_assets
            SET storage_status = 'AVAILABLE', sha256 = ?, size_bytes = ?, duration_ms = ?,
                triggered_at = ?, pre_buffer_sec = ?, post_buffer_sec = ?, termination_reason = ?
            WHERE id = ?
            """;

    private final S3Presigner presigner;
    private final S3Client s3Client;
    private final S3Properties props;
    private final JdbcTemplate jdbc;

    public MediaService(S3Presigner presigner, S3Client s3Client, S3Properties props, JdbcTemplate jdbc) {
        this.presigner = presigner;
        this.s3Client = s3Client;
        this.props = props;
        this.jdbc = jdbc;
    }

    /**
     * media_assets 행을 등록하고 업로드용 Presigned PUT URL 을 발급한다 (31-7 2·3단계).
     *
     * <p>같은 mediaId 재시도는 기존 행을 갱신하고 같은 key 로 새 URL 을 발급한다(31-10).
     * encounter 행이 먼저 있어야 mission_id 를 알 수 있으므로 encounter 적재가 선행이다.
     *
     * <p>같은 encounter·kind 에 다른 mediaId 로 발급을 요청하면 s3_key UNIQUE 충돌이므로
     * 409 로 거절한다(S15P11A301-154). 기존 행을 멱등 반환하지 않는 이유: 젯슨 업로더가
     * 응답의 mediaId 를 읽지 않아, 다른 id 의 행을 돌려주면 이후 complete 가 404 에 갇힌다.
     */
    public UploadUrlResponse createUpload(UploadUrlRequest request, Duration ttl) {
        UUID missionId = findMissionId(request.encounterId());
        String objectKey = objectKey(missionId, request.encounterId(), request.kind());
        String contentType = contentType(request.kind());

        try {
            jdbc.update(UPSERT_ASSET, request.mediaId(), missionId, request.encounterId(),
                    request.kind(), objectKey, request.sha256(), request.sizeBytes());
        } catch (DuplicateKeyException e) {
            throw new BusinessException(ErrorCode.MEDIA_KEY_CONFLICT,
                    "이미 다른 mediaId 로 등록된 저장 위치입니다: " + objectKey);
        }

        String url = presignPut(objectKey, contentType, ttl);
        return new UploadUrlResponse(request.mediaId(), objectKey, url, contentType, ttl.toSeconds());
    }

    /**
     * 업로드 완료를 반영해 storage_status 를 AVAILABLE 로 바꾼다 (31-7 5단계).
     *
     * <p>멱등하다. 이미 AVAILABLE 이면 스토리지 재검증 없이 같은 응답을 돌려준다 — 젯슨은
     * 응답을 못 받으면 같은 mediaId 로 재시도하며, 두 번째가 실패하면 영원히 PENDING 에
     * 갇힌다. 단 메타데이터 UPDATE 는 다시 수행한다 — 완료 통지가 유실돼 MediaReconciler 가
     * 메타데이터 없이 승격한 행을 젯슨의 뒤늦은 재시도가 채우는 경로다.
     *
     * <p>완료 검증은 HeadObject 존재·크기 비교 + 실물 SHA-256 비교다. 크기만 비교하던
     * #132 를 마무리한 것 — 같은 크기의 손상은 크기 비교로 잡히지 않는다. 체크섬이
     * 다르면 FAILED 로 남기고 400 을 돌려준다. 젯슨이 다시 올린 뒤 재호출하면 복구된다.
     *
     * <p>FAILED 행의 complete 도 같은 검증을 거쳐 AVAILABLE 이 된다 — FAILED 는
     * 서버 관점의 유실 판정일 뿐 종착역이 아니다.
     */
    public MediaCompleteResponse completeUpload(UUID mediaId, MediaCompleteRequest request) {
        List<AssetRow> rows = jdbc.query(
                "SELECT s3_key, storage_status FROM media_assets WHERE id = ?",
                (rs, i) -> new AssetRow(rs.getString("s3_key"), rs.getString("storage_status")),
                mediaId);
        if (rows.isEmpty()) {
            throw new BusinessException(ErrorCode.MEDIA_NOT_FOUND);
        }
        AssetRow asset = rows.getFirst();
        if ("AVAILABLE".equals(asset.storageStatus())) {
            updateCompleted(mediaId, request);
            return new MediaCompleteResponse(mediaId, asset.s3Key(), "AVAILABLE");
        }

        verifyUploaded(asset.s3Key(), request.sizeBytes());
        verifyChecksum(mediaId, asset.s3Key(), request.sha256());

        updateCompleted(mediaId, request);
        return new MediaCompleteResponse(mediaId, asset.s3Key(), "AVAILABLE");
    }

    private void updateCompleted(UUID mediaId, MediaCompleteRequest request) {
        MediaCompleteRequest.Recorded recorded = request.recorded();
        jdbc.update(COMPLETE_ASSET,
                request.sha256(),
                request.sizeBytes(),
                request.durationSeconds() == null ? null : Math.round(request.durationSeconds() * 1000),
                recorded == null || recorded.detectedAt() == null ? null : Timestamp.from(recorded.detectedAt()),
                recorded == null ? null : recorded.preRollSeconds(),
                recorded == null ? null : recorded.postRollSeconds(),
                recorded == null ? null : recorded.endReason(),
                mediaId);
    }

    /** 조회용 Presigned GET URL 발급. key 가 아니라 mediaId 로 조회한다(27.4). */
    public PresignedUrlResponse createViewUrl(UUID mediaId, Duration ttl) {
        List<String> keys = jdbc.query(
                "SELECT s3_key FROM media_assets WHERE id = ?",
                (rs, i) -> rs.getString("s3_key"), mediaId);
        if (keys.isEmpty()) {
            throw new BusinessException(ErrorCode.MEDIA_NOT_FOUND);
        }
        String objectKey = keys.getFirst();

        GetObjectRequest get = GetObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey)
                .build();
        GetObjectPresignRequest presignRequest = GetObjectPresignRequest.builder()
                .signatureDuration(ttl)
                .getObjectRequest(get)
                .build();
        String url = presigner.presignGetObject(presignRequest).url().toString();
        return new PresignedUrlResponse(objectKey, url, ttl.toSeconds());
    }

    private UUID findMissionId(UUID encounterId) {
        List<UUID> missionIds = jdbc.query(
                "SELECT mission_id FROM encounters WHERE id = ?",
                (rs, i) -> rs.getObject("mission_id", UUID.class), encounterId);
        if (missionIds.isEmpty()) {
            throw new BusinessException(ErrorCode.ENCOUNTER_NOT_FOUND);
        }
        return missionIds.getFirst();
    }

    /** 29.6 S3 key 규칙. 결정적이라 재시도에도 같은 key 가 나온다. */
    private String objectKey(UUID missionId, UUID encounterId, String kind) {
        String fileName = switch (kind) {
            case "EVENT_VIDEO" -> "event.mp4";
            case "THUMBNAIL" -> "thumbnail.jpg";
            // 잡음 제거 오디오(S15P11A301-228). 원본 오디오에서 파생 — 원본이 증거, 이건 청취 보조.
            case "EVENT_AUDIO_DENOISED" -> "event-denoised.m4a";
            default -> throw new IllegalArgumentException("지원하지 않는 kind: " + kind);
        };
        return "missions/%s/encounters/%s/%s".formatted(missionId, encounterId, fileName);
    }

    private String contentType(String kind) {
        return switch (kind) {
            case "EVENT_VIDEO" -> "video/mp4";
            case "EVENT_AUDIO_DENOISED" -> "audio/mp4";
            default -> "image/jpeg";
        };
    }

    private void verifyUploaded(String objectKey, long expectedSize) {
        HeadObjectResponse head;
        try {
            head = s3Client.headObject(b -> b.bucket(props.bucket()).key(objectKey));
        } catch (NoSuchKeyException e) {
            throw new BusinessException(ErrorCode.MEDIA_UPLOAD_INCOMPLETE,
                    "스토리지에 객체가 없습니다: " + objectKey);
        }
        if (head.contentLength() == null || head.contentLength() != expectedSize) {
            throw new BusinessException(ErrorCode.MEDIA_UPLOAD_INCOMPLETE,
                    "객체 크기가 다릅니다. 요청 %d, 실제 %s".formatted(expectedSize, head.contentLength()));
        }
    }

    private void verifyChecksum(UUID mediaId, String objectKey, String expectedSha256) {
        String actual = hashObject(objectKey);
        if (!actual.equalsIgnoreCase(expectedSha256)) {
            // 화면이 "유실"로 정직하게 보이도록 FAILED 를 남긴다. 재업로드 후
            // complete 재호출이 성공하면 AVAILABLE 로 덮인다.
            jdbc.update("UPDATE media_assets SET storage_status = 'FAILED' WHERE id = ?", mediaId);
            throw new BusinessException(ErrorCode.MEDIA_CHECKSUM_MISMATCH,
                    "체크섬 불일치. 통지 %s, 실물 %s".formatted(expectedSha256, actual));
        }
    }

    /** 저장된 객체의 SHA-256. MinIO 가 같은 호스트라 GET 스트리밍은 로컬 트래픽이다. */
    String hashObject(String objectKey) {
        GetObjectRequest get = GetObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey)
                .build();
        try (var in = s3Client.getObject(get)) {
            return Checksums.sha256Hex(in);
        } catch (IOException e) {
            // 읽기 실패는 업로드 잘못이 아니라 스토리지 문제다. 5xx 로 돌려
            // 젯슨 Outbox 가 재시도하게 한다 (4xx 는 영구 실패 처리, 29.4).
            throw new BusinessException(ErrorCode.INTERNAL_SERVER_ERROR,
                    "객체를 읽지 못했습니다: " + objectKey);
        }
    }

    private String presignPut(String objectKey, String contentType, Duration ttl) {
        PutObjectRequest put = PutObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey)
                .contentType(contentType)
                .build();
        PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
                .signatureDuration(ttl)
                .putObjectRequest(put)
                .build();
        return presigner.presignPutObject(presignRequest).url().toString();
    }

    private record AssetRow(String s3Key, String storageStatus) {
    }
}

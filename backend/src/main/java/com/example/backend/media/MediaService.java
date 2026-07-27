package com.example.backend.media;

import java.time.Duration;

import org.springframework.stereotype.Service;

import com.example.backend.common.config.S3Properties;

import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;

/**
 * S3(MinIO) 미디어 객체에 대한 Presigned URL 발급 서비스.
 * MVP 단계에서는 DB 연계 없이 객체 key 기준으로 업로드/조회 URL만 생성한다.
 */
@Service
public class MediaService {

    private final S3Presigner presigner;
    private final S3Properties props;

    public MediaService(S3Presigner presigner, S3Properties props) {
        this.presigner = presigner;
        this.props = props;
    }

    /** 업로드(PUT)용 Presigned URL 발급. */
    public String createUploadUrl(String objectKey, String contentType, Duration ttl) {
        PutObjectRequest.Builder put = PutObjectRequest.builder()
                .bucket(props.bucket())
                .key(objectKey);
        if (contentType != null && !contentType.isBlank()) {
            put.contentType(contentType);
        }
        PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
                .signatureDuration(ttl)
                .putObjectRequest(put.build())
                .build();
        return presigner.presignPutObject(presignRequest).url().toString();
    }

    /** 조회(GET)용 Presigned URL 발급. */
    public String createViewUrl(String objectKey, Duration ttl) {
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
}

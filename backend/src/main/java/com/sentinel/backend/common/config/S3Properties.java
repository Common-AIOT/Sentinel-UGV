package com.sentinel.backend.common.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * S3(MinIO) 연동 설정. application.yml 의 {@code app.s3.*} 값을 바인딩한다.
 */
@ConfigurationProperties(prefix = "app.s3")
public record S3Properties(
        String endpoint,
        String region,
        String bucket,
        String accessKey,
        String secretKey
) {
}

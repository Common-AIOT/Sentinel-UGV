package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 업로드용 Presigned URL 발급 응답.
 *
 * <p>{@code objectKey} 와 {@code contentType} 은 서버가 결정한 권위 값이다(31-11).
 * Presigned PUT 의 SigV4 서명에 Content-Type 헤더가 포함되므로, 젯슨은 응답의
 * {@code contentType} 을 PUT 헤더에 그대로 써야 한다. 다르면 403 이 난다.
 *
 * @param mediaId      요청의 미디어 식별자
 * @param objectKey    서버가 29.6 규칙으로 만든 객체 key
 * @param url          Presigned PUT URL
 * @param contentType  서버가 결정한 Content-Type
 * @param expiresInSec URL 유효 시간(초)
 */
public record UploadUrlResponse(
        UUID mediaId,
        String objectKey,
        String url,
        String contentType,
        long expiresInSec
) {
}

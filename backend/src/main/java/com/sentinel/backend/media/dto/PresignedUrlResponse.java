package com.sentinel.backend.media.dto;

/**
 * Presigned URL 발급 응답.
 *
 * @param objectKey    대상 객체 key
 * @param url          발급된 Presigned URL
 * @param expiresInSec URL 유효 시간(초)
 */
public record PresignedUrlResponse(
        String objectKey,
        String url,
        long expiresInSec
) {
}

package com.example.backend.media.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * 업로드용 Presigned URL 발급 요청.
 *
 * @param objectKey   버킷 내 객체 경로 (예: missions/{id}/encounters/{id}/event.mp4)
 * @param contentType 업로드 파일의 Content-Type (선택)
 */
public record UploadUrlRequest(
        @NotBlank String objectKey,
        String contentType
) {
}

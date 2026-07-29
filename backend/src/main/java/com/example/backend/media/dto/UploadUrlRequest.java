package com.example.backend.media.dto;

import java.util.UUID;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

/**
 * 업로드용 Presigned URL 발급 요청 (명세 31-7 2단계).
 * 계약: {@code common/schemas/media-upload-request.schema.json}
 *
 * <p>object key 는 서버가 29.6 규칙으로 결정한다(31-11). {@code suggestedKey} 는 힌트일 뿐
 * 서버가 무시해도 되고, 젯슨은 응답의 {@code objectKey} 를 권위로 삼는다.
 *
 * @param encounterId 이 미디어가 속한 encounter
 * @param mediaId     젯슨이 만든 미디어 식별자. 재시도에도 같은 값이라 중복 등록을 막는다(31-10)
 * @param kind        EVENT_VIDEO 또는 THUMBNAIL. 이벤트 하나에 두 객체가 따로 올라온다
 * @param fileName    원본 파일명
 * @param sizeBytes   업로드할 바이트 수. 완료 시점에 실제 크기와 비교한다
 * @param sha256      파일의 SHA-256 소문자 16진수
 * @param contentType 생략 가능. 서버가 kind 로 결정한 응답 값이 권위다
 * @param suggestedKey 젯슨이 제안하는 object key. 서버는 쓰지 않는다
 */
public record UploadUrlRequest(
        @NotNull UUID encounterId,
        @NotNull UUID mediaId,
        @NotBlank @Pattern(regexp = "EVENT_VIDEO|THUMBNAIL") String kind,
        @NotBlank @Size(max = 255) String fileName,
        @NotNull @PositiveOrZero Long sizeBytes,
        @NotBlank @Pattern(regexp = "^[0-9a-f]{64}$") String sha256,
        String contentType,
        @Size(max = 512) String suggestedKey
) {
}

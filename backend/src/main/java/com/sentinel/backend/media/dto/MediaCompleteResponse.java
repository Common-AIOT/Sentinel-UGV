package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 업로드 완료 통지 응답. 같은 mediaId 로 재시도해도 같은 응답을 돌려준다(멱등, 31-10).
 *
 * @param mediaId       미디어 식별자
 * @param objectKey     스토리지 객체 key
 * @param storageStatus 처리 후 상태. 항상 AVAILABLE 이다
 */
public record MediaCompleteResponse(
        UUID mediaId,
        String objectKey,
        String storageStatus
) {
}

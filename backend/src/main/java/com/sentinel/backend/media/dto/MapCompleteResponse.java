package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 지도 업로드 완료 응답 (S15P11A301-185). 재호출에도 같은 응답을 돌려준다(멱등).
 */
public record MapCompleteResponse(
        UUID mapId
) {
}

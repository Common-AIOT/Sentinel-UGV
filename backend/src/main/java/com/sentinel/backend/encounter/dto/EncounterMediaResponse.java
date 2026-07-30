package com.sentinel.backend.encounter.dto;

import java.util.UUID;

/**
 * encounter 에 연결된 미디어 한 건.
 *
 * <p>재생은 {@code GET /api/v1/media/{mediaId}/view-url} 로 한다. storageStatus 가
 * AVAILABLE 이 아니면 아직 업로드 전이라 재생 링크 발급이 실패할 수 있다.
 */
public record EncounterMediaResponse(
        UUID mediaId,
        String type,
        String storageStatus,
        Long durationMs) {
}

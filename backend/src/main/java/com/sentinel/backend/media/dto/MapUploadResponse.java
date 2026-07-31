package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 지도 업로드 URL 발급 응답 (S15P11A301-185).
 *
 * <p>mapId 가 이 지도의 공식 식별자다 — 젯슨은 이 값을 telemetry·encounter 의
 * {@code pose.mapId} 에 싣는다(#171). 두 PUT 모두 Content-Type 헤더에 응답의
 * contentType 값을 그대로 써야 한다(다르면 403).
 *
 * @param mapId        지도 식별자 (maps.id)
 * @param pgmKey       pgm 객체 key (서버 결정, 29.6 스타일)
 * @param yamlKey      yaml 객체 key
 * @param pgmUrl       pgm 업로드용 Presigned PUT URL
 * @param yamlUrl      yaml 업로드용 Presigned PUT URL
 * @param contentType  두 PUT 에 공통으로 쓸 Content-Type
 * @param expiresInSec URL 유효 시간(초)
 */
public record MapUploadResponse(
        UUID mapId,
        String pgmKey,
        String yamlKey,
        String pgmUrl,
        String yamlUrl,
        String contentType,
        long expiresInSec
) {
}

package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 지도 조회 응답 (S15P11A301-187). 렌더링에는 두 파일이 모두 필요하다 —
 * pgm 은 격자 이미지, yaml 은 해상도·원점 메타데이터.
 *
 * @param mapId        지도 식별자
 * @param pgmUrl       pgm 다운로드용 Presigned GET URL
 * @param yamlUrl      yaml 다운로드용 Presigned GET URL
 * @param expiresInSec URL 유효 시간(초)
 */
public record MapViewResponse(
        UUID mapId,
        String pgmUrl,
        String yamlUrl,
        long expiresInSec
) {
}

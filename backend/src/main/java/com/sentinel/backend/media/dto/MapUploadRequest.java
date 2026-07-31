package com.sentinel.backend.media.dto;

import java.util.UUID;

import jakarta.validation.constraints.NotNull;

/**
 * 지도 업로드 URL 발급 요청 (S15P11A301-185).
 *
 * <p>missionId 만 계약이다. 젯슨이 참고로 보내는 파일 크기 필드(pgmSizeBytes 등)는
 * 저장할 곳이 없어 받지 않는다 — Jackson 이 모르는 필드를 무시하므로 보내도 무해하다.
 *
 * @param missionId 이 지도가 속한 임무
 */
public record MapUploadRequest(
        @NotNull UUID missionId
) {
}

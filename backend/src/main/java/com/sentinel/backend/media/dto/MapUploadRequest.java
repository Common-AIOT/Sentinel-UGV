package com.sentinel.backend.media.dto;

import java.util.UUID;

import jakarta.validation.constraints.NotNull;

/**
 * 지도 업로드 URL 발급 요청 (S15P11A301-185).
 *
 * <p>젯슨이 참고로 보내는 파일 크기 필드(pgmSizeBytes 등)는 저장할 곳이 없어 받지
 * 않는다 — Jackson 이 모르는 필드를 무시하므로 보내도 무해하다.
 *
 * @param missionId 이 지도가 속한 임무
 * @param mapId     선택. 젯슨이 SLAM 세션 시작 때 생성한 지도 식별자(S15P11A301-189).
 *                  임무 중 telemetry·encounter 의 pose.mapId 와 등록된 maps.id 를
 *                  일치시키기 위해 받는다(encounterId·mediaId 와 같은 29.3 패턴).
 *                  없으면 서버가 생성한다.
 */
public record MapUploadRequest(
        @NotNull UUID missionId,
        UUID mapId
) {
}

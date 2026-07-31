package com.sentinel.backend.media.dto;

import java.util.UUID;

/**
 * 지도 조회 응답 (S15P11A301-187, 메타데이터는 S15P11A301-197).
 *
 * <p>메타데이터(resolution·origin·크기)가 있으면 프론트는 yaml 을 받을 필요가 없다 —
 * 젯슨이 완료 본문에 실은 전정밀 값이라 yaml(유효숫자 3자리 절단)보다 정확하다.
 * 완료 이전이거나 구버전 젯슨의 지도면 null 이며, 그때는 yamlUrl 로 폴백한다.
 *
 * @param mapId        지도 식별자
 * @param pgmUrl       pgm 다운로드용 Presigned GET URL
 * @param yamlUrl      yaml 다운로드용 Presigned GET URL
 * @param expiresInSec URL 유효 시간(초)
 * @param resolution   m/셀 (예: 0.05)
 * @param originX      지도 원점 x (map 좌표계, 미터)
 * @param originY      지도 원점 y
 * @param originYaw    지도 원점 yaw (항상 0 — slam_toolbox 가 회전 격자를 만들지 않음)
 * @param width        가로 셀 수
 * @param height       세로 셀 수
 */
public record MapViewResponse(
        UUID mapId,
        String pgmUrl,
        String yamlUrl,
        long expiresInSec,
        Double resolution,
        Double originX,
        Double originY,
        Double originYaw,
        Integer width,
        Integer height
) {
}

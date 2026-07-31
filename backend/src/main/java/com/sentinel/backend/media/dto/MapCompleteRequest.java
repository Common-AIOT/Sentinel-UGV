package com.sentinel.backend.media.dto;

import jakarta.validation.constraints.Pattern;

/**
 * 지도 업로드 완료 본문 (S15P11A301-197). 전부 선택이며 본문 자체를 생략해도 된다
 * (하위 호환 — 그때는 스토리지의 yaml 을 파싱해 채운다).
 *
 * <p>필드명은 젯슨 map_uploader 가 보내는 페이로드와 글자 단위로 일치해야 한다 —
 * Jackson 이 모르는 필드를 무시하므로 이름이 어긋나면 200 이면서 값이 잘린 채 남는다.
 *
 * <p>origin·resolution 은 젯슨이 live OccupancyGrid 에서 읽은 전정밀 값이다.
 * yaml 파일의 origin 은 유효숫자 3자리로 잘리므로 이 본문 값이 권위다.
 * originYaw 는 구조적으로 항상 0 이다(slam_toolbox 가 회전 격자를 만들지 않음).
 */
public record MapCompleteRequest(
        Double resolution,
        Double originX,
        Double originY,
        Double originYaw,
        Integer width,
        Integer height,
        @Pattern(regexp = "^[0-9a-f]{64}$") String pgmSha256,
        @Pattern(regexp = "^[0-9a-f]{64}$") String yamlSha256
) {
}

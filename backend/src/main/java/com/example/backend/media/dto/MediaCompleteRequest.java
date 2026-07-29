package com.example.backend.media.dto;

import java.time.Instant;
import java.util.UUID;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

/**
 * 업로드 완료 통지 요청 (명세 31-7 5단계).
 * 계약: {@code common/schemas/media-complete-request.schema.json}
 *
 * <p>젯슨이 스토리지에 직접 올리므로 이 호출이 없으면 서버는 업로드가 끝났는지 모른다.
 * 처리 후 {@code media_assets.storage_status} 가 AVAILABLE 이 된다(13.6).
 *
 * <p>{@code recorded.personCount} 는 저장하지 않는다. 인원 수는 encounter 적재
 * (ENCOUNTER_CONFIRMED)가 관리한다.
 *
 * @param encounterId     이 미디어가 속한 encounter
 * @param objectKey       발급 응답에서 받은 값을 그대로 돌려준다
 * @param sizeBytes       실제로 올린 바이트 수. 스토리지 실물과 비교한다
 * @param sha256          발급 요청 때와 같은 체크섬. 저장해 두고 나중에 무결성 검증에 쓴다
 * @param kind            EVENT_VIDEO 또는 THUMBNAIL (선택)
 * @param durationSeconds 영상 길이. 썸네일이면 null
 * @param recorded        녹화 구간 메타데이터 (선택)
 */
public record MediaCompleteRequest(
        @NotNull UUID encounterId,
        @NotBlank @Size(max = 512) String objectKey,
        @NotNull @PositiveOrZero Long sizeBytes,
        @NotBlank @Pattern(regexp = "^[0-9a-f]{64}$") String sha256,
        @Pattern(regexp = "EVENT_VIDEO|THUMBNAIL") String kind,
        @PositiveOrZero Double durationSeconds,
        Recorded recorded
) {

    /** 녹화 구간 메타데이터. 다시보기에서 사전·사후 구간을 표시하는 데 쓴다. */
    public record Recorded(
            Instant detectedAt,
            Double preRollSeconds,
            Double postRollSeconds,
            String endReason,
            Integer personCount
    ) {
    }
}

package com.sentinel.backend.control.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;

/**
 * 제어권 요청. {@code robotId} 는 로봇 이름 문자열이다(예: SENTINEL-01).
 */
public record ControlSessionRequest(
        @Schema(description = "로봇 이름 문자열. UUID 가 아니다", example = "SENTINEL-01")
        @NotBlank String robotId) {
}

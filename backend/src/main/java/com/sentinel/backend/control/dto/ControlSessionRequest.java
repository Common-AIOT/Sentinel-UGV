package com.sentinel.backend.control.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * 제어권 요청. {@code robotId} 는 로봇 이름 문자열이다(예: SENTINEL-01).
 */
public record ControlSessionRequest(@NotBlank String robotId) {
}

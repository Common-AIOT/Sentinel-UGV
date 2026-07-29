package com.sentinel.backend.control.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * 임무 제어 명령 요청 (명세 27.4).
 */
public record IssueCommandRequest(
        @NotBlank @Pattern(regexp = "START|PAUSE|RESUME|RETURN|STOP") String type
) {
}

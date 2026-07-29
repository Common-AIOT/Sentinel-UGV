package com.sentinel.backend.mission.dto;

import jakarta.validation.constraints.NotBlank;

/**
 * 임무 생성 요청.
 *
 * <p>{@code robotId} 는 MQTT 봉투의 robotId 와 같은 로봇 이름 문자열이다(예: SENTINEL-01).
 * DB 의 {@code robots.id}(UUID) 가 아니라 {@code robots.name} 으로 조회한다.
 */
public record CreateMissionRequest(@NotBlank String robotId) {
}

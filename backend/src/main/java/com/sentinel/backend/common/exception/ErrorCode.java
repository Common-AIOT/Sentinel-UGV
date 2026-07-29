package com.sentinel.backend.common.exception;

import lombok.Getter;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;

@Getter
@RequiredArgsConstructor
public enum ErrorCode {
    // 공통
    INTERNAL_SERVER_ERROR(HttpStatus.INTERNAL_SERVER_ERROR, "COMMON-001", "서버 내부 오류가 발생했습니다."),
    INVALID_INPUT_VALUE(HttpStatus.BAD_REQUEST, "COMMON-002", "잘못된 입력값입니다."),
    METHOD_NOT_ALLOWED(HttpStatus.METHOD_NOT_ALLOWED, "COMMON-003", "지원하지 않는 HTTP 메서드입니다."),
    URL_NOT_FOUND(HttpStatus.NOT_FOUND, "COMMON-004", "요청하신 URL을 찾을 수 없습니다."),
    FILE_SIZE_EXCEEDED(HttpStatus.PAYLOAD_TOO_LARGE, "COMMON-005", "업로드 가능한 파일 용량을 초과했습니다."),

    // 인증/인가
    UNAUTHORIZED(HttpStatus.UNAUTHORIZED, "AUTH-001", "인증되지 않은 사용자입니다."),
    INVALID_TOKEN(HttpStatus.UNAUTHORIZED, "AUTH-002", "유효하지 않은 토큰입니다."),
    EXPIRED_TOKEN(HttpStatus.UNAUTHORIZED, "AUTH-003", "만료된 토큰입니다."),
    ACCESS_DENIED(HttpStatus.FORBIDDEN, "AUTH-004", "해당 자원에 대한 접근 권한이 없습니다."),

    // 미디어 (31-7 업로드 계약)
    ENCOUNTER_NOT_FOUND(HttpStatus.NOT_FOUND, "ENCOUNTER-001", "encounter 를 찾을 수 없습니다. encounter 적재가 선행되어야 합니다."),
    MEDIA_NOT_FOUND(HttpStatus.NOT_FOUND, "MEDIA-001", "미디어 자산을 찾을 수 없습니다."),
    MEDIA_UPLOAD_INCOMPLETE(HttpStatus.BAD_REQUEST, "MEDIA-002", "스토리지에서 업로드를 확인하지 못했습니다."),

    // 로봇/임무
    ROBOT_NOT_FOUND(HttpStatus.NOT_FOUND, "ROBOT-001", "등록된 로봇을 찾을 수 없습니다."),
    MISSION_NOT_FOUND(HttpStatus.NOT_FOUND, "MISSION-001", "임무를 찾을 수 없습니다."),
    MISSION_ALREADY_ACTIVE(HttpStatus.CONFLICT, "MISSION-002", "해당 로봇에 진행 중인 임무가 이미 있습니다."),
    MISSION_ALREADY_ENDED(HttpStatus.CONFLICT, "MISSION-003", "이미 종료된 임무입니다."),

    // 제어 (11.4, 27.5)
    CONTROL_SESSION_DENIED(HttpStatus.CONFLICT, "CONTROL-001", "다른 운영자가 제어권을 보유 중입니다."),
    CONTROL_SESSION_NOT_FOUND(HttpStatus.NOT_FOUND, "CONTROL-002", "제어 세션을 찾을 수 없습니다."),
    BROKER_UNAVAILABLE(HttpStatus.SERVICE_UNAVAILABLE, "MQTT-001", "메시지 브로커에 연결할 수 없어 명령을 전달하지 못했습니다.");

    private final HttpStatus httpStatus;
    private final String code;
    private final String message;
}

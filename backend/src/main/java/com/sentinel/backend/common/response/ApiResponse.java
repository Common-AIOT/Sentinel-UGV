package com.sentinel.backend.common.response;

import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 프론트엔드와 통신하기 위한 공통 응답 포맷 클래스
 */
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ApiResponse<T> {

    private String status;
    private String message;
    private T data;

    private ApiResponse(String status, String message, T data) {
        this.status = status;
        this.message = message;
        this.data = data;
    }

    // 성공 응답 (데이터 포함)
    public static <T> ApiResponse<T> success(T data) {
        return new ApiResponse<>("SUCCESS", "요청이 성공적으로 처리되었습니다.", data);
    }

    // 성공 응답 (메시지 및 데이터 포함)
    public static <T> ApiResponse<T> success(String message, T data) {
        return new ApiResponse<>("SUCCESS", message, data);
    }

    // 성공 응답 (데이터가 없을 때)
    public static ApiResponse<?> success() {
        return new ApiResponse<>("SUCCESS", "요청이 성공적으로 처리되었습니다.", null);
    }

    // 에러 응답
    public static ApiResponse<?> error(String message) {
        return new ApiResponse<>("ERROR", message, null);
    }

    // 에러 응답 (커스텀 에러 코드 포함)
    public static ApiResponse<?> error(String code, String message) {
        return new ApiResponse<>(code, message, null);
    }
}

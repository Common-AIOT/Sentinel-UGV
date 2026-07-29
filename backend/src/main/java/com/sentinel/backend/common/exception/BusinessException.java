package com.sentinel.backend.common.exception;

import lombok.Getter;

/**
 * 프로젝트 내에서 발생하는 비즈니스 로직 관련 예외의 최상위 클래스
 */
@Getter
public class BusinessException extends RuntimeException {

    private final ErrorCode errorCode;

    public BusinessException(ErrorCode errorCode) {
        super(errorCode.getMessage());
        this.errorCode = errorCode;
    }

    public BusinessException(ErrorCode errorCode, String customMessage) {
        super(customMessage);
        this.errorCode = errorCode;
    }
}

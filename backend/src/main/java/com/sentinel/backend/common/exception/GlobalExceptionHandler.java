package com.sentinel.backend.common.exception;

import java.util.stream.Collectors;

import com.sentinel.backend.common.response.ApiResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/**
 * 프로젝트 전역에서 발생하는 예외를 한 곳에서 처리하여
 * 일관된 형태(ApiResponse)로 프론트엔드에 전달하는 핸들러
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    // 0. 비즈니스 로직 예외 처리
    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<ApiResponse<?>> handleBusinessException(BusinessException e) {
        log.warn("비즈니스 예외 발생: {}", e.getMessage());
        ErrorCode errorCode = e.getErrorCode();
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(), e.getMessage()));
    }

    // 1. 가장 흔한 잘못된 파라미터나 로직 오류 처리 (400 Bad Request)
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ApiResponse<?>> handleIllegalArgumentException(IllegalArgumentException e) {
        log.warn("잘못된 요청 파라미터: {}", e.getMessage());
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(ApiResponse.error(e.getMessage()));
    }

    // 1-1. @Valid 본문 검증 실패와 본문 파싱 실패 (400 Bad Request).
    //      젯슨 업로더는 4xx 를 받아야 잘못된 요청의 재시도를 멈춘다(31-7).
    //      이것이 500 으로 흐르면 일시 장애로 오인해 무한 재시도에 갇힌다.
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResponse<?>> handleMethodArgumentNotValid(MethodArgumentNotValidException e) {
        String detail = e.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + " " + error.getDefaultMessage())
                .collect(Collectors.joining(", "));
        log.warn("요청 본문 검증 실패: {}", detail);
        ErrorCode errorCode = ErrorCode.INVALID_INPUT_VALUE;
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(),
                        detail.isBlank() ? errorCode.getMessage() : detail));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiResponse<?>> handleHttpMessageNotReadable(HttpMessageNotReadableException e) {
        log.warn("요청 본문 파싱 실패: {}", e.getMessage());
        ErrorCode errorCode = ErrorCode.INVALID_INPUT_VALUE;
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(), errorCode.getMessage()));
    }

    // 1-2. 경로 변수 타입 불일치 (400). UUID 자리에 "abc" 같은 값이 오는 경우로,
    //      클라이언트의 요청 실수가 500 으로 보이면 안 된다(S15P11A301-184).
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<ApiResponse<?>> handleTypeMismatch(MethodArgumentTypeMismatchException e) {
        log.warn("경로 변수 타입 불일치: {}={}", e.getName(), e.getValue());
        ErrorCode errorCode = ErrorCode.INVALID_INPUT_VALUE;
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(),
                        "%s 값의 형식이 올바르지 않습니다: %s".formatted(e.getName(), e.getValue())));
    }

    // 1-3. 지원하지 않는 HTTP 메서드 (405). COMMON-003 이 정의만 있고 안 쓰이던 것을 연결한다.
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ApiResponse<?>> handleMethodNotSupported(HttpRequestMethodNotSupportedException e) {
        log.warn("지원하지 않는 HTTP 메서드: {}", e.getMethod());
        ErrorCode errorCode = ErrorCode.METHOD_NOT_ALLOWED;
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(), errorCode.getMessage()));
    }

    // 1-4. 존재하지 않는 URL (404). 핸들러가 없으면 정적 리소스 해석까지 실패한 뒤
    //      NoResourceFoundException 이 오는데, catch-all 로 흐르면 오타 URL 이 500 이 된다.
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ApiResponse<?>> handleNoResourceFound(NoResourceFoundException e) {
        log.warn("존재하지 않는 URL: /{}", e.getResourcePath());
        ErrorCode errorCode = ErrorCode.URL_NOT_FOUND;
        return ResponseEntity.status(errorCode.getHttpStatus())
                .body(ApiResponse.error(errorCode.getCode(), errorCode.getMessage()));
    }

    // 2. 그 외에 잡히지 않은 모든 서버 에러 처리 (500 Internal Server Error)
    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<?>> handleException(Exception e) {
        log.error("서버 내부 오류 발생: ", e);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.error("서버 내부 오류가 발생했습니다. 관리자에게 문의해주세요."));
    }
}

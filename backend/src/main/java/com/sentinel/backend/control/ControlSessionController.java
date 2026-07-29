package com.sentinel.backend.control;

import java.util.UUID;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.control.dto.ControlSessionRequest;
import com.sentinel.backend.control.dto.ControlSessionResponse;

import jakarta.validation.Valid;

/**
 * 제어권 세션 API (명세 11.4, 27.4). 로봇 한 대에 유효 세션은 1개다.
 */
@RestController
@RequestMapping("/api/v1/control-sessions")
public class ControlSessionController {

    private final ControlSessionService sessionService;

    public ControlSessionController(ControlSessionService sessionService) {
        this.sessionService = sessionService;
    }

    /** 제어권 요청. 다른 유효 세션이 있으면 409 CONTROL_SESSION_DENIED. */
    @PostMapping
    public ApiResponse<ControlSessionResponse> issue(@Valid @RequestBody ControlSessionRequest request) {
        return ApiResponse.success(sessionService.issue(request.robotId()));
    }

    /** 제어권 반납. */
    @DeleteMapping("/{sessionId}")
    public ApiResponse<?> release(@PathVariable UUID sessionId) {
        sessionService.release(sessionId);
        return ApiResponse.success();
    }
}

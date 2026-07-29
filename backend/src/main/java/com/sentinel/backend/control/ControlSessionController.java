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

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * 제어권 세션 API (명세 11.4, 27.4). 로봇 한 대에 유효 세션은 1개다.
 */
@Tag(name = "제어권", description = "로봇을 조종할 권한입니다. 한 번에 한 명만 가질 수 있습니다.")
@RestController
@RequestMapping("/api/v1/control-sessions")
public class ControlSessionController {

    private final ControlSessionService sessionService;

    public ControlSessionController(ControlSessionService sessionService) {
        this.sessionService = sessionService;
    }

    /** 제어권 요청. 다른 유효 세션이 있으면 409 CONTROL_SESSION_DENIED. */
    @Operation(summary = "제어권 요청",
            description = "조종을 시작하기 전에 제어권을 받습니다. 응답의 sessionId 가 내 제어권 번호이고 10분 뒤 자동으로 풀립니다. "
                    + "다른 사람이 이미 갖고 있으면 409 가 납니다. robotId 에는 SENTINEL-01 처럼 로봇 이름을 넣습니다.")
    @PostMapping
    public ApiResponse<ControlSessionResponse> issue(@Valid @RequestBody ControlSessionRequest request) {
        return ApiResponse.success(sessionService.issue(request.robotId()));
    }

    /** 제어권 반납. */
    @Operation(summary = "제어권 반납",
            description = "조종이 끝나면 제어권을 돌려줍니다. 그래야 다른 사람이 바로 조종할 수 있습니다. 이미 반납됐거나 모르는 번호면 404 가 납니다.")
    @DeleteMapping("/{sessionId}")
    public ApiResponse<?> release(@PathVariable UUID sessionId) {
        sessionService.release(sessionId);
        return ApiResponse.success();
    }
}

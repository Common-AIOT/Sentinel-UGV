package com.sentinel.backend.control;

import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.control.dto.CommandResponse;
import com.sentinel.backend.control.dto.IssueCommandRequest;

import jakarta.validation.Valid;

/**
 * 임무 제어 명령 API (명세 27.4).
 * 202 는 전달 시작이며, 완결은 젯슨의 COMMAND_ACK 가 결정한다.
 */
@RestController
@RequestMapping("/api/v1/missions/{missionId}/commands")
public class MissionCommandController {

    private final MissionCommandService commandService;

    public MissionCommandController(MissionCommandService commandService) {
        this.commandService = commandService;
    }

    /** START/PAUSE/RESUME/RETURN/STOP 명령을 젯슨에 전달한다. */
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public ApiResponse<CommandResponse> issue(
            @PathVariable UUID missionId,
            @Valid @RequestBody IssueCommandRequest request) {
        return ApiResponse.success(commandService.issue(missionId, request.type()));
    }
}

package com.sentinel.backend.control;

import java.util.List;
import java.util.UUID;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.control.dto.CommandResponse;
import com.sentinel.backend.control.dto.CommandStatusResponse;
import com.sentinel.backend.control.dto.IssueCommandRequest;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * 임무 제어 명령 API (명세 27.4).
 * 202 는 전달 시작이며, 완결은 젯슨의 COMMAND_ACK 가 결정한다.
 */
@Tag(name = "임무 제어",
        description = "로봇에게 임무 명령(시작·일시정지·재개·복귀·정지)과 모드 전환(수동·자율)을 보냅니다.")
@RestController
@RequestMapping("/api/v1/missions/{missionId}/commands")
public class MissionCommandController {

    private final MissionCommandService commandService;

    public MissionCommandController(MissionCommandService commandService) {
        this.commandService = commandService;
    }

    /** START/PAUSE/RESUME/RETURN/STOP/MANUAL/AUTO 명령을 젯슨에 전달한다. */
    @Operation(summary = "임무 명령 보내기 (START/PAUSE/RESUME/RETURN/STOP/MANUAL/AUTO)",
            description = "로봇에게 시작(START)·일시정지(PAUSE)·재개(RESUME)·복귀(RETURN)·정지(STOP)를 시키거나, "
                    + "수동 전환(MANUAL)·자율 복귀(AUTO)로 조종 권한을 옮깁니다. "
                    + "202 응답은 '로봇에게 보냈다'는 뜻이고, 로봇이 실제로 받아들이면 그때 임무 상태가 바뀝니다. "
                    + "이미 끝난 임무면 409, 로봇과의 통신 경로가 끊겨 있으면 503(명령이 전달되지 않음)이 납니다.\n\n"
                    + "AUTO 는 임무 상태를 PAUSED 로 되돌릴 뿐 주행을 재개하지 않습니다 — 다시 움직이려면 "
                    + "이어서 RESUME 을 보냅니다. 또한 AUTO 는 모터 보드가 거부할 수 있는 유일한 명령입니다: "
                    + "모바일 조종 입력이 최근 0.5초 안에 있었으면 REJECTED/MANUAL_INPUT_ACTIVE 로 끝납니다.")
    @PostMapping
    @ResponseStatus(HttpStatus.ACCEPTED)
    public ApiResponse<CommandResponse> issue(
            @PathVariable UUID missionId,
            @Valid @RequestBody IssueCommandRequest request) {
        return ApiResponse.success(commandService.issue(missionId, request.type()));
    }

    /** 보낸 명령들이 수락·실행·거부됐는지 조회한다 (S15P11A301-207). */
    @Operation(summary = "명령 처리 결과 조회",
            description = "이 임무로 보낸 명령들의 처리 상태를 최신순으로 줍니다. "
                    + "result: PENDING(로봇 회신 대기) → ACCEPTED/EXECUTED(수락·실행) 또는 "
                    + "REJECTED(거부)·EXPIRED(만료)·FAILED(전달 실패). "
                    + "거부·실패면 reasonCode(MANUAL_INPUT_ACTIVE·MOTOR_BOARD_NO_ACK·ESTOP_ACTIVE·"
                    + "INVALID_STATE·NOT_IMPLEMENTED 등)에 이유가 담기고, 성공이면 null 입니다. "
                    + "202 를 받은 명령이 실제로 어떻게 끝났는지는 여기서 확인합니다. 없는 임무면 404.")
    @GetMapping
    public ApiResponse<List<CommandStatusResponse>> list(@PathVariable UUID missionId) {
        return ApiResponse.success(commandService.findCommands(missionId));
    }
}

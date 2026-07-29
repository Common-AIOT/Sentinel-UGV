package com.sentinel.backend.mission;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.mission.dto.CreateMissionRequest;
import com.sentinel.backend.mission.dto.MissionDetailResponse;
import com.sentinel.backend.mission.dto.MissionSummaryResponse;
import com.sentinel.backend.mission.dto.TelemetryPointResponse;

import jakarta.validation.Valid;

/**
 * 임무 생성·목록·상세와 시계열 그래프 조회 API.
 * 규범: 통합 명세서 27.4.
 */
@RestController
@RequestMapping("/api/v1/missions")
public class MissionController {

    private final MissionService missionService;

    public MissionController(MissionService missionService) {
        this.missionService = missionService;
    }

    /** 임무 생성. missionId 는 서버가 생성한다(31-4). */
    @PostMapping
    public ApiResponse<MissionDetailResponse> create(@Valid @RequestBody CreateMissionRequest request) {
        return ApiResponse.success(missionService.create(request.robotId()));
    }

    /** 임무 목록 (최신순). */
    @GetMapping
    public ApiResponse<List<MissionSummaryResponse>> list() {
        return ApiResponse.success(missionService.findAll());
    }

    /** 임무 상세·요약. */
    @GetMapping("/{missionId}")
    public ApiResponse<MissionDetailResponse> detail(@PathVariable UUID missionId) {
        return ApiResponse.success(missionService.findDetail(missionId));
    }

    /**
     * 시계열 범위 조회. robot_metrics 를 time_bucket 구간 평균으로 내려준다.
     * from·to 를 생략하면 임무 창(started_at~ended_at)이 기본값이다.
     */
    @GetMapping("/{missionId}/telemetry")
    public ApiResponse<List<TelemetryPointResponse>> telemetry(
            @PathVariable UUID missionId,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant to,
            @RequestParam(defaultValue = "10") int bucketSeconds) {
        return ApiResponse.success(missionService.findTelemetry(missionId, from, to, bucketSeconds));
    }
}

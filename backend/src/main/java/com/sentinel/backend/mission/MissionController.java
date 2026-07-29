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

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * 임무 생성·목록·상세와 시계열 그래프 조회 API.
 * 규범: 통합 명세서 27.4.
 */
@Tag(name = "임무", description = "임무를 만들고 조회합니다. 만든 임무를 시작·정지시키는 건 '임무 제어' API 입니다.")
@RestController
@RequestMapping("/api/v1/missions")
public class MissionController {

    private final MissionService missionService;

    public MissionController(MissionService missionService) {
        this.missionService = missionService;
    }

    /** 임무 생성. missionId 는 서버가 생성한다(31-4). */
    @Operation(summary = "임무 생성",
            description = "새 임무를 만들고 임무 번호(missionId)를 돌려줍니다. robotId 에는 SENTINEL-01 처럼 로봇 이름을 넣습니다. "
                    + "로봇 한 대는 임무를 하나만 진행할 수 있어서, 아직 안 끝난 임무가 있으면 409 가 납니다. "
                    + "등록 안 된 로봇 이름이면 404 가 납니다.")
    @PostMapping
    public ApiResponse<MissionDetailResponse> create(@Valid @RequestBody CreateMissionRequest request) {
        return ApiResponse.success(missionService.create(request.robotId()));
    }

    /** 임무 목록 (최신순). */
    @Operation(summary = "임무 목록",
            description = "지금까지의 임무를 최신순으로 보여줍니다(최대 100건). "
                    + "걸린 시간·이동 거리·발견 인원 수는 임무가 끝난 뒤에 채워지고, 그 전에는 null 입니다.")
    @GetMapping
    public ApiResponse<List<MissionSummaryResponse>> list() {
        return ApiResponse.success(missionService.findAll());
    }

    /** 임무 상세·요약. */
    @Operation(summary = "임무 상세",
            description = "임무 하나의 현재 상태, 시작·종료 시각, 종료 사유, 결과 요약(걸린 시간·거리·발견 인원)을 봅니다. "
                    + "homePose 는 로봇 출발 위치입니다(임무 시작 전이면 null).")
    @GetMapping("/{missionId}")
    public ApiResponse<MissionDetailResponse> detail(@PathVariable UUID missionId) {
        return ApiResponse.success(missionService.findDetail(missionId));
    }

    /**
     * 시계열 범위 조회. robot_metrics 를 time_bucket 구간 평균으로 내려준다.
     * from·to 를 생략하면 임무 창(started_at~ended_at)이 기본값이다.
     */
    @Operation(summary = "시계열 그래프 조회",
            description = "로봇의 CPU·GPU·메모리·온도·배터리 기록을 그래프 그리기 좋게 구간 평균으로 잘라서 줍니다. "
                    + "bucketSeconds=10 이면 10초 단위 평균 한 점씩입니다. 기간(from/to)을 안 주면 임무 시작~종료 구간을 줍니다. "
                    + "battery 는 ESP32 연동 전까지 null 로 옵니다.")
    @GetMapping("/{missionId}/telemetry")
    public ApiResponse<List<TelemetryPointResponse>> telemetry(
            @PathVariable UUID missionId,
            @Parameter(description = "조회 시작(ISO-8601, 예: 2026-07-29T04:00:00Z). 생략 시 임무 시작 시각")
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant from,
            @Parameter(description = "조회 끝. 생략 시 임무 종료 시각(진행 중이면 현재)")
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant to,
            @Parameter(description = "집계 버킷 크기(초), 1 이상. 기본 10")
            @RequestParam(defaultValue = "10") int bucketSeconds) {
        return ApiResponse.success(missionService.findTelemetry(missionId, from, to, bucketSeconds));
    }
}

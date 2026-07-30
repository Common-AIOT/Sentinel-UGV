package com.sentinel.backend.encounter;

import java.util.List;
import java.util.UUID;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.encounter.dto.EncounterDetailResponse;
import com.sentinel.backend.encounter.dto.EncounterSummaryResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;

/**
 * 발견(encounter) 조회 API.
 * 규범: 통합 명세서 27.4 / 31-7.
 */
@Tag(name = "발견", description = "임무 중 발견한 사람(그룹) 기록을 조회합니다. 적재는 젯슨이 MQTT events 로 보냅니다.")
@RestController
public class EncounterController {

    private final EncounterQueryService encounterQueryService;

    public EncounterController(EncounterQueryService encounterQueryService) {
        this.encounterQueryService = encounterQueryService;
    }

    /** 임무별 발견 목록. */
    @Operation(summary = "임무별 발견 목록",
            description = "임무 하나에서 발견한 사람(그룹) 목록을 최신순으로 줍니다. 지도 마커와 발견 목록 화면에 씁니다. "
                    + "endedAt 이 null 이면 아직 진행 중인 발견입니다. 없는 임무면 404, 발견이 없으면 빈 목록입니다.")
    @GetMapping("/api/v1/missions/{missionId}/encounters")
    public ApiResponse<List<EncounterSummaryResponse>> listByMission(@PathVariable UUID missionId) {
        return ApiResponse.success(encounterQueryService.findByMission(missionId));
    }

    /** 발견 상세와 연결된 미디어 목록. */
    @Operation(summary = "발견 상세",
            description = "발견 하나의 위치·인원 수·시각과 연결된 영상 목록을 줍니다. "
                    + "영상 재생은 media 항목의 mediaId 로 '재생 링크 발급' API 를 부르면 됩니다. "
                    + "storageStatus 가 AVAILABLE 인 것만 재생 가능합니다.")
    @GetMapping("/api/v1/encounters/{encounterId}")
    public ApiResponse<EncounterDetailResponse> detail(@PathVariable UUID encounterId) {
        return ApiResponse.success(encounterQueryService.findDetail(encounterId));
    }
}

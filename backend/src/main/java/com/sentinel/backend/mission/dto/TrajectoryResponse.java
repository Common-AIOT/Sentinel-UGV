package com.sentinel.backend.mission.dto;

import java.util.List;
import java.util.UUID;

/**
 * 임무 궤적 (S15P11A301-194). mapId 는 이 궤적과 같은 좌표계인 지도의 식별자다 —
 * 프론트가 "지금 그리는 지도 위에 이 궤적을 얹어도 되는지" 확인하는 용도이며,
 * 지도 등록 전에는 null 이다.
 */
public record TrajectoryResponse(
        UUID mapId,
        List<TrajectoryPointResponse> points) {
}

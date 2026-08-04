package com.sentinel.backend.telemetry;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.telemetry.dto.TelemetryLatestResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;

/**
 * 임무와 무관한 텔레메트리 조회 (S15P11A301-255).
 * 임무 단위 이력 그래프는 {@code /missions/{id}/telemetry} 가 담당하고,
 * 여기는 "지금 값" 하나만 담당한다.
 */
@Tag(name = "실시간 센서", description = "임무가 없어도 로봇의 최신 센서 값을 조회합니다.")
@RestController
@RequestMapping("/api/v1/telemetry")
public class TelemetryQueryController {

    private final TelemetryQueryService service;

    public TelemetryQueryController(TelemetryQueryService service) {
        this.service = service;
    }

    @Operation(summary = "최신 센서 값 조회 (임무 무관)",
            description = "온습도(DHT11)와 MCU(ESP32) 연결 상태의 가장 최근 측정값을 줍니다. "
                    + "임무 밖 대기 중 값(mission_id 없는 telemetry)도 잡힙니다 — 그게 이 API 의 존재 이유입니다. "
                    + "값이 아예 없으면 404 가 아니라 200 에 null 입니다(없음은 정상 상태). "
                    + "값이 얼마나 오래됐는지는 environmentTime·mcuTime 으로 화면이 판단합니다.")
    @GetMapping("/latest")
    public ApiResponse<TelemetryLatestResponse> latest() {
        return ApiResponse.success(service.findLatest());
    }
}

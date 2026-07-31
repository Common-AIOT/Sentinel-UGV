package com.sentinel.backend.media;

import java.time.Duration;
import java.util.UUID;

import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.media.dto.MapCompleteResponse;
import com.sentinel.backend.media.dto.MapUploadRequest;
import com.sentinel.backend.media.dto.MapUploadResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * SLAM 지도 업로드 API (S15P11A301-185, 젯슨 #171 선행).
 * 규범: 13.2(maps)·29.6(object key)·31-10(지도 S3 보존). 27.4 에 없던 명세 공백 보완.
 */
@Tag(name = "지도", description = "임무가 끝나면 젯슨이 SLAM 지도(pgm+yaml)를 올립니다. 관제는 이 지도 위에 발견 위치를 그립니다.")
@RestController
@RequestMapping("/api/v1/maps")
public class MapUploadController {

    private static final Duration UPLOAD_TTL = Duration.ofMinutes(10);

    private final MapUploadService mapUploadService;

    public MapUploadController(MapUploadService mapUploadService) {
        this.mapUploadService = mapUploadService;
    }

    /** 발급은 멱등 — 같은 임무로 다시 부르면 같은 mapId 에 새 URL 을 준다. */
    @Operation(summary = "지도 업로드 주소 발급 (젯슨용)",
            description = "지도 파일 두 개(pgm·yaml)를 올릴 주소를 받습니다. 응답의 mapId 가 이 지도의 공식 번호라서, "
                    + "젯슨은 이 값을 telemetry 와 발견(pose.mapId)에 그대로 씁니다. "
                    + "같은 임무로 다시 불러도 안전합니다 — 같은 mapId 로 새 주소를 줍니다. "
                    + "PUT 의 Content-Type 은 응답 값을 그대로 써야 합니다(다르면 403). 없는 임무면 404.")
    @PostMapping("/uploads")
    public ApiResponse<MapUploadResponse> createUpload(@Valid @RequestBody MapUploadRequest request) {
        return ApiResponse.success(mapUploadService.createUpload(request.missionId(), UPLOAD_TTL));
    }

    /** 완료 통지. 두 객체의 실재를 확인한다. 재호출에도 같은 응답(멱등). */
    @Operation(summary = "지도 업로드 완료 알리기 (젯슨용)",
            description = "두 파일을 다 올린 뒤 부릅니다. 서버가 스토리지에 파일이 진짜 있는지 확인합니다. "
                    + "두 번 불러도 문제없습니다(둘 다 200). 파일이 없으면 400 — 다시 올린 뒤 또 부르면 됩니다.")
    @PostMapping("/uploads/{mapId}/complete")
    public ApiResponse<MapCompleteResponse> completeUpload(@PathVariable UUID mapId) {
        return ApiResponse.success(mapUploadService.completeUpload(mapId));
    }
}

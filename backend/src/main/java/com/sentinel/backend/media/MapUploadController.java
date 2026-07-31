package com.sentinel.backend.media;

import java.time.Duration;
import java.util.UUID;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.media.dto.MapCompleteRequest;
import com.sentinel.backend.media.dto.MapCompleteResponse;
import com.sentinel.backend.media.dto.MapUploadRequest;
import com.sentinel.backend.media.dto.MapUploadResponse;
import com.sentinel.backend.media.dto.MapViewResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * SLAM 지도 업로드 API (S15P11A301-185, 젯슨 #171 선행).
 * 규범: 13.2(maps)·29.6(object key)·31-10(지도 S3 보존). 27.4 에 없던 명세 공백 보완.
 */
@Tag(name = "지도", description = "임무가 끝나면 젯슨이 SLAM 지도(pgm+yaml)를 올립니다. 관제는 이 지도 위에 발견 위치를 그립니다.")
@RestController
public class MapUploadController {

    private static final Duration UPLOAD_TTL = Duration.ofMinutes(10);
    private static final Duration VIEW_TTL = Duration.ofMinutes(10);

    private final MapUploadService mapUploadService;

    public MapUploadController(MapUploadService mapUploadService) {
        this.mapUploadService = mapUploadService;
    }

    /** 발급은 멱등 — 같은 임무로 다시 부르면 같은 mapId 에 새 URL 을 준다. */
    @Operation(summary = "지도 업로드 주소 발급 (젯슨용)",
            description = "지도 파일 두 개(pgm·yaml)를 올릴 주소를 받습니다. 응답의 mapId 가 이 지도의 공식 번호라서, "
                    + "젯슨은 이 값을 telemetry 와 발견(pose.mapId)에 그대로 씁니다. "
                    + "요청에 mapId 를 담으면 그 값을 지도 번호로 씁니다 — SLAM 세션 시작 때 만든 번호를 "
                    + "임무 내내 쓰다가 등록까지 일치시키는 용도입니다. 안 담으면 서버가 만듭니다. "
                    + "같은 임무로 다시 불러도 안전합니다 — 같은 mapId 로 새 주소를 줍니다. "
                    + "PUT 의 Content-Type 은 응답 값을 그대로 써야 합니다(다르면 403). 없는 임무면 404.")
    @PostMapping("/api/v1/maps/uploads")
    public ApiResponse<MapUploadResponse> createUpload(@Valid @RequestBody MapUploadRequest request) {
        return ApiResponse.success(
                mapUploadService.createUpload(request.missionId(), request.mapId(), UPLOAD_TTL));
    }

    /** 완료 통지. 실재 확인 + 메타데이터 저장(재호출 시 갱신). */
    @Operation(summary = "지도 업로드 완료 알리기 (젯슨용)",
            description = "두 파일을 다 올린 뒤 부릅니다. 서버가 스토리지에 파일이 진짜 있는지 확인합니다. "
                    + "본문에 지도 메타데이터(resolution·originX/Y/Yaw·width·height·sha256)를 실으면 저장돼서 "
                    + "관제 조회에 그대로 나갑니다 — 본문 origin 이 yaml(유효숫자 3자리)보다 정밀합니다. "
                    + "본문을 생략하면 yaml 값으로 채웁니다. 두 번 불러도 문제없고, 재호출하면 메타데이터가 갱신됩니다. "
                    + "파일이 없으면 400 — 다시 올린 뒤 또 부르면 됩니다.")
    @PostMapping("/api/v1/maps/uploads/{mapId}/complete")
    public ApiResponse<MapCompleteResponse> completeUpload(
            @PathVariable UUID mapId,
            @RequestBody(required = false) @Valid MapCompleteRequest request) {
        return ApiResponse.success(mapUploadService.completeUpload(mapId, request));
    }

    /** 관제는 missionId 만 알고 시작하므로 임무 기준으로 조회한다 (S15P11A301-187). */
    @Operation(summary = "임무의 지도 조회 (관제용)",
            description = "임무의 SLAM 지도를 받을 10분짜리 링크 두 개(pgm·yaml)를 줍니다. "
                    + "지도를 그리려면 둘 다 필요합니다 — pgm 은 격자 이미지, yaml 은 해상도·원점입니다. "
                    + "링크가 만료되면 다시 부르면 됩니다. 지도가 아직 안 올라온 임무면 404 입니다.")
    @GetMapping("/api/v1/missions/{missionId}/map")
    public ApiResponse<MapViewResponse> viewMap(@PathVariable UUID missionId) {
        return ApiResponse.success(mapUploadService.createViewUrls(missionId, VIEW_TTL));
    }
}

package com.sentinel.backend.media;

import java.time.Duration;
import java.util.UUID;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.sentinel.backend.common.response.ApiResponse;
import com.sentinel.backend.media.dto.MediaCompleteRequest;
import com.sentinel.backend.media.dto.MediaCompleteResponse;
import com.sentinel.backend.media.dto.PresignedUrlResponse;
import com.sentinel.backend.media.dto.UploadUrlRequest;
import com.sentinel.backend.media.dto.UploadUrlResponse;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;

/**
 * 미디어(영상·스냅샷 등) 업로드·조회 API.
 * 규범: 통합 명세서 27.4 / 31-7 / 31-11.
 */
@Tag(name = "미디어", description = "이벤트 영상·썸네일을 올리고(젯슨용) 재생 링크를 받는(관제용) API 입니다.")
@RestController
@RequestMapping("/api/v1/media")
public class MediaController {

    private static final Duration UPLOAD_TTL = Duration.ofMinutes(10);
    private static final Duration VIEW_TTL = Duration.ofMinutes(10);

    private final MediaService mediaService;

    public MediaController(MediaService mediaService) {
        this.mediaService = mediaService;
    }

    /** 업로드용 Presigned PUT URL 발급. object key 는 서버가 결정한다(31-11). */
    @Operation(summary = "업로드 주소 발급 (젯슨용)",
            description = "젯슨이 영상 파일을 올리기 전에 업로드 주소(url)를 받습니다. 그 주소로 파일을 PUT 하면 스토리지에 직접 올라갑니다. "
                    + "주의 둘: ① Content-Type 헤더는 응답에 온 값을 그대로 써야 합니다(다르면 403) "
                    + "② 사람 발견 이벤트(encounter)가 서버에 먼저 접수돼 있어야 합니다(없으면 404). "
                    + "같은 mediaId 로 다시 불러도 안전합니다 — 같은 위치로 새 주소를 줍니다. "
                    + "단, 같은 encounter 에 다른 mediaId 로 부르면 409 입니다 — 새 mediaId 를 만들지 말고 기존 것으로 재시도하세요.")
    @PostMapping("/uploads")
    public ApiResponse<UploadUrlResponse> createUploadUrl(@Valid @RequestBody UploadUrlRequest request) {
        return ApiResponse.success(mediaService.createUpload(request, UPLOAD_TTL));
    }

    /** 업로드 완료 통지. 같은 mediaId 재시도에 같은 응답을 돌려준다(멱등, 31-10). */
    @Operation(summary = "업로드 완료 알리기 (젯슨용)",
            description = "파일을 다 올린 뒤 '올렸다'고 서버에 알립니다. 서버가 파일이 진짜 있는지·크기가 맞는지 확인하고 재생 가능 상태로 바꿉니다. "
                    + "실수로 두 번 불러도 문제없습니다(둘 다 200). 파일이 없거나 크기가 다르면 400 — 다시 올린 뒤 또 부르면 됩니다.")
    @PostMapping("/uploads/{mediaId}/complete")
    public ApiResponse<MediaCompleteResponse> completeUpload(
            @PathVariable UUID mediaId,
            @Valid @RequestBody MediaCompleteRequest request) {
        return ApiResponse.success(mediaService.completeUpload(mediaId, request));
    }

    /** 조회용 Presigned GET URL 발급. */
    @Operation(summary = "재생 링크 발급 (관제용)",
            description = "영상을 볼 수 있는 10분짜리 링크를 받습니다. 관제 화면의 영상 재생·다운로드에 씁니다. 링크가 만료되면 다시 부르면 됩니다.")
    @GetMapping("/{mediaId}/view-url")
    public ApiResponse<PresignedUrlResponse> createViewUrl(@PathVariable UUID mediaId) {
        return ApiResponse.success(mediaService.createViewUrl(mediaId, VIEW_TTL));
    }
}

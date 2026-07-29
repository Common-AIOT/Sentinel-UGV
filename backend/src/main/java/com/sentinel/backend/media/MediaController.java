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

import jakarta.validation.Valid;

/**
 * 미디어(영상·스냅샷 등) 업로드·조회 API.
 * 규범: 통합 명세서 27.4 / 31-7 / 31-11.
 */
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
    @PostMapping("/uploads")
    public ApiResponse<UploadUrlResponse> createUploadUrl(@Valid @RequestBody UploadUrlRequest request) {
        return ApiResponse.success(mediaService.createUpload(request, UPLOAD_TTL));
    }

    /** 업로드 완료 통지. 같은 mediaId 재시도에 같은 응답을 돌려준다(멱등, 31-10). */
    @PostMapping("/uploads/{mediaId}/complete")
    public ApiResponse<MediaCompleteResponse> completeUpload(
            @PathVariable UUID mediaId,
            @Valid @RequestBody MediaCompleteRequest request) {
        return ApiResponse.success(mediaService.completeUpload(mediaId, request));
    }

    /** 조회용 Presigned GET URL 발급. */
    @GetMapping("/{mediaId}/view-url")
    public ApiResponse<PresignedUrlResponse> createViewUrl(@PathVariable UUID mediaId) {
        return ApiResponse.success(mediaService.createViewUrl(mediaId, VIEW_TTL));
    }
}

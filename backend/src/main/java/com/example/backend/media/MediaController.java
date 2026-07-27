package com.example.backend.media;

import java.time.Duration;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.example.backend.common.response.ApiResponse;
import com.example.backend.media.dto.PresignedUrlResponse;
import com.example.backend.media.dto.UploadUrlRequest;

import jakarta.validation.Valid;

/**
 * 미디어(영상·스냅샷 등) Presigned URL 발급 API.
 * 규범: 통합 명세서 27.4 / 31-7.
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

    /** 업로드용 Presigned PUT URL 발급. */
    @PostMapping("/uploads")
    public ApiResponse<PresignedUrlResponse> createUploadUrl(@Valid @RequestBody UploadUrlRequest request) {
        String url = mediaService.createUploadUrl(request.objectKey(), request.contentType(), UPLOAD_TTL);
        return ApiResponse.success(
                new PresignedUrlResponse(request.objectKey(), url, UPLOAD_TTL.toSeconds()));
    }

    /** 조회용 Presigned GET URL 발급. */
    @GetMapping("/view-url")
    public ApiResponse<PresignedUrlResponse> createViewUrl(@RequestParam("key") String objectKey) {
        String url = mediaService.createViewUrl(objectKey, VIEW_TTL);
        return ApiResponse.success(
                new PresignedUrlResponse(objectKey, url, VIEW_TTL.toSeconds()));
    }
}

package com.example.backend.media;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;

import org.junit.jupiter.api.Test;

import com.example.backend.media.dto.MediaCompleteRequest;
import com.example.backend.media.dto.UploadUrlRequest;

import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * 미디어 업로드 REST 계약({@code common/schemas/media-*-request.schema.json})을
 * 백엔드 DTO 가 그대로 파싱하는지 검증한다 (S15P11A301-132).
 *
 * <p>예제는 {@code common/samples/media/} 에 있다. MQTT 봉투 예제와 달리 REST 본문이라
 * 최상위 {@code common/samples} 에 두면 봉투 검증(validate_schemas.py·MessageContractTest)이
 * 깨지므로 하위 디렉터리를 쓴다.
 *
 * <p>클래스 이름이 {@code *MessageContractTest} 로 끝나야 CI 의 {@code test:backend} 가 실행한다.
 */
class MediaMessageContractTest {

    private static final Path SAMPLES = Path.of("..", "common", "samples", "media");

    // 계약이 additionalProperties: false 이므로 DTO 가 빠뜨린 필드가 생기면 깨져야 한다.
    private final ObjectMapper mapper = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    @Test
    void uploadRequestSampleParsesIntoDto() throws Exception {
        UploadUrlRequest request = read("upload-request.json", UploadUrlRequest.class);

        assertEquals("c81f6d20-5a47-4e93-b2d8-1f70e4a95c33", request.encounterId().toString());
        assertEquals("3d0a2c5e-8f14-4b6a-9c27-5e81d4f0a912", request.mediaId().toString());
        assertEquals("EVENT_VIDEO", request.kind());
        assertEquals("event.mp4", request.fileName());
        assertEquals(2775794L, request.sizeBytes());
        assertEquals(64, request.sha256().length());
        // suggestedKey 는 힌트일 뿐이다. 파싱은 되어야 하지만 서버는 쓰지 않는다(31-11).
        assertTrue(request.suggestedKey().startsWith("SENTINEL-01/"));
    }

    @Test
    void completeRequestSampleParsesIntoDto() throws Exception {
        MediaCompleteRequest request = read("complete-request.json", MediaCompleteRequest.class);

        assertEquals("c81f6d20-5a47-4e93-b2d8-1f70e4a95c33", request.encounterId().toString());
        assertTrue(request.objectKey().startsWith("missions/"));
        assertEquals(2775794L, request.sizeBytes());
        assertEquals(12.4, request.durationSeconds());
        assertEquals(Instant.parse("2026-07-28T07:31:02.140Z"), request.recorded().detectedAt());
        assertEquals("PERSON_LOST", request.recorded().endReason());
        assertEquals(1, request.recorded().personCount());
    }

    private <T> T read(String fileName, Class<T> type) throws Exception {
        Path sample = SAMPLES.resolve(fileName);
        assertTrue(Files.exists(sample), fileName + " sample is missing");
        return mapper.readValue(Files.readString(sample), type);
    }
}

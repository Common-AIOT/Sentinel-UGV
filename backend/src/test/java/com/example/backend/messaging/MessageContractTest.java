package com.example.backend.messaging;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

import org.junit.jupiter.api.Test;

import com.example.backend.messaging.dto.MessageEnvelope;
import com.example.backend.messaging.dto.PresenceData;
import com.example.backend.messaging.dto.TelemetryData;

import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * 젯슨과 공유하는 메시지 계약({@code common/schemas})을 백엔드 DTO 가 그대로 파싱하는지 검증한다.
 *
 * <p>CI 의 {@code test:message-contract} job 은 Python 쪽만 검사한다. 젯슨이 필드를 바꾸면
 * Java DTO 는 그대로 통과해 런타임에야 터지므로, 여기서 예제 메시지를 직접 파싱해 막는다.
 *
 * <p>알 수 없는 필드에서 실패하는 기본 설정을 그대로 쓴다. 계약이 {@code additionalProperties:
 * false} 이므로, DTO 가 빠뜨린 필드가 생기면 이 테스트가 깨져야 한다.
 */
class MessageContractTest {

    private static final Path SAMPLES = Path.of("..", "common", "samples");

    // 알 수 없는 필드에서 실패하도록 명시한다. 계약이 additionalProperties: false 이므로
    // DTO 가 빠뜨린 필드가 생기면 이 테스트가 깨져야 한다.
    private final ObjectMapper mapper = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    @Test
    void 모든_예제_메시지가_공통_봉투로_파싱된다() throws Exception {
        List<Path> samples = sampleFiles();
        assertFalse(samples.isEmpty(), "common/samples 에 예제 메시지가 없다");

        for (Path sample : samples) {
            MessageEnvelope envelope =
                    mapper.readValue(Files.readString(sample), MessageEnvelope.class);
            assertNotNull(envelope.messageId(), sample + ": messageId 가 비었다");
            assertNotNull(envelope.messageType(), sample + ": messageType 이 비었다");
            assertNotNull(envelope.robotId(), sample + ": robotId 가 비었다");
            assertNotNull(envelope.sentAt(), sample + ": sentAt 파싱 실패");
            assertNotNull(envelope.data(), sample + ": data 가 비었다");
        }
    }

    @Test
    void ESP32_미연동_telemetry_는_본문_그룹이_null_로_온다() throws Exception {
        TelemetryData data = telemetry("telemetry-esp32-absent.json");

        // 지금 실제로 오는 형태다. null 을 정상 입력으로 다루지 못하면 수집이 전부 실패한다.
        assertNull(data.pose());
        assertNull(data.motion());
        assertNull(data.battery());
        assertNull(data.environment());
        assertNotNull(data.compute(), "compute 는 젯슨이 항상 채운다");
        assertEquals(31.5, data.compute().cpuPercent());

        // false(확인했고 끊김)와 null(확인 수단 없음)은 다르다.
        assertNull(data.health().mcuConnected());
        assertTrue(data.health().lidarOk());
    }

    @Test
    void ESP32_연동_telemetry_는_모든_본문_그룹이_채워진다() throws Exception {
        TelemetryData data = telemetry("telemetry-esp32-connected.json");

        assertEquals(4.31, data.pose().x());
        assertEquals(0.24, data.motion().linearVelocityMps());
        assertEquals(72.0, data.battery().percent());
        assertEquals(28.4, data.environment().temperatureC());
        assertEquals("EXPLORING", data.missionState());
    }

    @Test
    void 임무_외_telemetry_는_missionId_가_null_이다() throws Exception {
        MessageEnvelope envelope = envelope("telemetry-esp32-absent.json");

        // V2 마이그레이션이 hypertable 의 mission_id 를 nullable 로 바꾼 근거다.
        assertNull(envelope.missionId());
    }

    @Test
    void LWT_presence_는_OFFLINE_과_단절_사유를_담는다() throws Exception {
        MessageEnvelope envelope = envelope("presence-offline-lwt.json");
        PresenceData data = mapper.treeToValue(envelope.data(), PresenceData.class);

        assertEquals(PresenceData.STATUS_OFFLINE, data.status());
        assertEquals("MQTT_CONNECTION_LOST", data.reason());
    }

    @Test
    void 정상_접속_presence_는_ONLINE_이다() throws Exception {
        MessageEnvelope envelope = envelope("presence-online.json");
        PresenceData data = mapper.treeToValue(envelope.data(), PresenceData.class);

        assertEquals(PresenceData.STATUS_ONLINE, data.status());
        assertNull(data.reason());
    }

    private List<Path> sampleFiles() throws Exception {
        try (Stream<Path> files = Files.list(SAMPLES)) {
            return files.filter(p -> p.toString().endsWith(".json")).sorted().toList();
        }
    }

    private MessageEnvelope envelope(String fileName) throws Exception {
        Path sample = SAMPLES.resolve(fileName);
        assertTrue(Files.exists(sample), fileName + " 예제가 없다");
        return mapper.readValue(Files.readString(sample), MessageEnvelope.class);
    }

    private TelemetryData telemetry(String fileName) throws Exception {
        return mapper.treeToValue(envelope(fileName).data(), TelemetryData.class);
    }
}

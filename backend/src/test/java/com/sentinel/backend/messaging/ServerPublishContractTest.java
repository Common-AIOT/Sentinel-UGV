package com.sentinel.backend.messaging;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.UUID;

import org.junit.jupiter.api.Test;

import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SchemaValidatorsConfig;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.MissionCommandData;

import tools.jackson.databind.json.JsonMapper;

/**
 * 서버가 발행하는 메시지가 {@code common/schemas} 계약을 지키는지 검증한다 (S15P11A301-288).
 *
 * <p>{@link MessageContractTest} 와 방향이 반대다. 그쪽은 <b>젯슨이 보낸 예제를 백엔드가
 * 읽을 수 있는가</b>(입력)를 보고, 여기서는 <b>백엔드가 만든 JSON 이 계약에 맞는가</b>(출력)를
 * 본다. 지금까지 출력 쪽은 아무도 보지 않았다.
 *
 * <p>실제로 물렸다. MVP 주에 서버가 {@code sentAt} 을 나노초(9자리)로 내보내 봉투 스키마
 * (소수점 이하 6자리)를 위반했고, 발행물을 사람이 눈으로 보다가 우연히 잡았다. 젯슨이
 * 형식 위반 메시지를 조용히 버리면 "명령을 보냈는데 로봇이 반응하지 않는다"가 되고,
 * 원인은 어디에도 표시되지 않는다.
 *
 * <p>스키마를 다시 적지 않고 {@code common/schemas} 파일을 그대로 읽는다 — 계약이 바뀌면
 * 이 시험이 함께 움직여야 한다. 직렬화는 운영과 같은 Jackson 3 매퍼를 쓰고, 검증기
 * (networknt, Jackson 2 기반)에는 문자열로 넘겨 두 매퍼가 섞이지 않게 한다.
 */
class ServerPublishContractTest {

    private static final Path SCHEMAS = Path.of("..", "common", "schemas");

    /** 운영 발행 경로(MqttGateway.publish)와 같은 매퍼 설정이어야 의미가 있다. */
    private final tools.jackson.databind.ObjectMapper publisher = JsonMapper.builder().build();

    private final com.fasterxml.jackson.databind.ObjectMapper validatorMapper =
            new com.fasterxml.jackson.databind.ObjectMapper();

    @Test
    void missionCommandEnvelopeSatisfiesEnvelopeSchema() throws Exception {
        String json = publisher.writeValueAsString(missionCommandEnvelope("START"));
        assertValid("envelope.schema.json", json);
    }

    @Test
    void missionCommandDataSatisfiesCommandSchema() throws Exception {
        // 봉투의 data 본문만 떼어 본문 스키마로 검증한다.
        String json = publisher.writeValueAsString(new MissionCommandData(UUID.randomUUID(), "STOP"));
        assertValid("mission-command.schema.json", json);
    }

    @Test
    void allMissionCommandTypesSatisfyContract() throws Exception {
        // 목록을 하드코딩하지 않고 **스키마에서 읽는다** (S15P11A301-298). 배열을
        // 손으로 관리하면 계약에 명령이 추가될 때마다 여기를 함께 고쳐야 하고,
        // 안 고치면 새 명령은 검사되지 않은 채로 통과한다.
        List<String> types = commandTypeEnum();

        // 빈 읽기도 실패해야 한다. 경로가 틀리거나 스키마 모양이 바뀌면 위 리스트가
        // 조용히 비고, 그러면 반복문이 0회 돌아 "전부 통과"로 보인다.
        assertTrue(types.contains("START"), "스키마에서 명령 enum 을 읽지 못했다: " + types);
        assertTrue(types.contains("AUTO"), "MANUAL/AUTO 가 계약에 없다: " + types);

        for (String type : types) {
            assertValid("envelope.schema.json",
                    publisher.writeValueAsString(missionCommandEnvelope(type)));
            assertValid("mission-command.schema.json",
                    publisher.writeValueAsString(new MissionCommandData(UUID.randomUUID(), type)));
        }
    }

    @Test
    void serverCommandVocabularyMatchesTheContract() throws Exception {
        // 서버가 아는 어휘와 계약의 어휘가 정확히 같아야 한다. 한쪽만 늘면
        // 관제 버튼이 400 을 받거나(서버가 모름), 젯슨이 봉투를 버린다(계약 위반).
        Set<String> contract = Set.copyOf(commandTypeEnum());
        Set<String> server = Set.of(
                MissionCommandData.TYPE_START,
                MissionCommandData.TYPE_PAUSE,
                MissionCommandData.TYPE_RESUME,
                MissionCommandData.TYPE_RETURN,
                MissionCommandData.TYPE_STOP,
                MissionCommandData.TYPE_MANUAL,
                MissionCommandData.TYPE_AUTO);
        assertEquals(contract, server);
    }

    private List<String> commandTypeEnum() throws Exception {
        Path path = SCHEMAS.resolve("mission-command.schema.json");
        assertTrue(Files.exists(path), "mission-command.schema.json 스키마가 없다");
        List<String> types = new ArrayList<>();
        validatorMapper.readTree(Files.readString(path))
                .path("properties").path("type").path("enum")
                .forEach(node -> types.add(node.asText()));
        return types;
    }

    @Test
    void sentAtIsTruncatedToMillis() throws Exception {
        // 재발 방지 대상 그 자체다(MVP 주 나노초 위반). 스키마 pattern 이 소수점 이하
        // 6자리까지만 허용하는데 Jackson 의 Instant 기본 직렬화는 9자리를 낼 수 있다.
        String json = publisher.writeValueAsString(missionCommandEnvelope("START"));
        String sentAt = validatorMapper.readTree(json).get("sentAt").asText();
        assertTrue(sentAt.matches("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,6})?Z$"),
                "sentAt 이 봉투 계약 형식을 벗어났다: " + sentAt);
    }

    @Test
    void nanosecondSentAtIsRejected() throws Exception {
        // 검사 자체가 살아 있는지 확인한다. 이 시험이 없으면 스키마가 느슨해져도
        // 위 시험들이 조용히 통과해 "검사하고 있다"는 착각만 남는다.
        String json = publisher.writeValueAsString(missionCommandEnvelope("START"))
                .replaceFirst("\"sentAt\":\"[^\"]+\"", "\"sentAt\":\"2026-08-05T01:02:03.123456789Z\"");
        Set<ValidationMessage> errors = validate("envelope.schema.json", json);
        assertFalse(errors.isEmpty(), "나노초 sentAt 이 통과했다 — 계약 검사가 동작하지 않는다");
    }

    /**
     * <b>운영 발행 코드 그 자체를 부른다.</b> 시험이 봉투를 따로 만들면 발행부가 규칙을
     * 어겨도(예: sentAt 절단 삭제) 시험은 통과한다 — 검사한다는 착각만 남는다.
     */
    private MessageEnvelope missionCommandEnvelope(String type) {
        return MessageEnvelope.forPublish(
                MessageEnvelope.TYPE_MISSION_COMMAND,
                "SENTINEL-01",
                UUID.randomUUID(),
                publisher.valueToTree(new MissionCommandData(UUID.randomUUID(), type)));
    }

    private void assertValid(String schemaFile, String json) throws Exception {
        Set<ValidationMessage> errors = validate(schemaFile, json);
        assertTrue(errors.isEmpty(), schemaFile + " 위반: " + errors + "\n발행물: " + json);
    }

    private Set<ValidationMessage> validate(String schemaFile, String json) throws Exception {
        Path path = SCHEMAS.resolve(schemaFile);
        assertTrue(Files.exists(path), schemaFile + " 스키마가 없다");
        JsonSchema schema = JsonSchemaFactory
                .getInstance(SpecVersion.VersionFlag.V202012)
                .getSchema(Files.readString(path), SchemaValidatorsConfig.builder().build());
        return schema.validate(validatorMapper.readTree(json));
    }

    @Test
    void schemaVersionMatchesContract() {
        // 봉투 스키마가 요구하는 형식(^[0-9]+\.[0-9]+$)을 상수가 지키는지.
        assertEquals("1.0", MessageEnvelope.SCHEMA_VERSION);
        assertTrue(MessageEnvelope.SCHEMA_VERSION.matches("^[0-9]+\\.[0-9]+$"));
    }
}

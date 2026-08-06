package com.sentinel.backend.telemetry.dto;

import java.time.Instant;

/**
 * 임무와 무관한 최신 센서 값 (S15P11A301-255). 대기 중 관제 카드의 출처다.
 *
 * <p>두 하이퍼테이블의 최신 1행씩이라 시각이 그룹별로 따로 간다 — DHT11 이 죽으면
 * environment 행은 안 쌓이는데 mcu 는 계속 신선한 식으로 어긋날 수 있어서, 단일
 * time 으로는 프런트의 60초 신선도 판정이 한쪽에서 틀어진다.
 *
 * <p>데이터가 아예 없으면 필드가 null 이다 — 404 가 아니라 200 이다. 없음은 오류가
 * 아니라 정상 상태고, 신선도 판정은 프런트가 한다(정책 이원화 방지).
 *
 * <p>주행 지표(linearVelocity·angularVelocity)는 S15P11A301-300 에서 더했다. 다른
 * 하이퍼테이블({@code robot_pose})에서 오므로 시각이 또 따로 간다. 원천은 후륜 엔코더를
 * ESP32 가 계수하고 젯슨이 역산한 오도메트리라, 센서 보드가 빠지면 온습도와 함께 빈다.
 */
public record TelemetryLatestResponse(
        Instant environmentTime,
        Double temperature,
        Double humidity,
        Instant mcuTime,
        Boolean mcuConnected,
        // 녹화기 상태 (S15P11A301-310). mcuTime 과 같은 행에서 온다 — 신선도도 그것을 쓴다.
        // 두 필드는 독립이며 합치지 않는다: recorderOk=true 와 사유가 함께 오는 것이
        // 「지금은 정상이지만 이번 기동에 실패가 있었다」는 정상 조합이다.
        Boolean recorderOk,
        String recorderLastFailure,
        Instant poseTime,
        Double linearVelocity,
        Double angularVelocity) {
}

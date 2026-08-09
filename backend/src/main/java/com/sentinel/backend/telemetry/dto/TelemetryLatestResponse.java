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
        /**
         * 제어 모드 MANUAL·AUTO·null (S15P11A301-350).
         *
         * <p><b>이 응답에서 유일하게 하이퍼테이블이 아닌 값이다</b> — {@code robots} 에서
         * 온다. 제어 모드는 임무에도 시계열에도 매이지 않기 때문이다. 임무가 닫힌 뒤에
         * 사람이 폰을 잡는 것이 이 값이 필요한 대표 상황인데, 그 구간에는 telemetry 가
         * 아예 쌓이지 않는다.
         *
         * <p>{@code null} 은 「모름」이며 <b>AUTO 로 뭉개면 안 된다.</b> 그렇게 하면
         * 수동 조종 중에도 화면이 「자율」을 띄우는데, 그것이 이 필드를 만든 이유다.
         */
        String controlMode,
        Instant environmentTime,
        Double temperature,
        Double humidity,
        Instant mcuTime,
        Boolean mcuConnected,
        // 모터 보드 링크 (S15P11A301-317). mcuConnected 는 **센서 보드**(엔코더 발행자)이고
        // 이 값은 **모터 보드**다. 보드가 둘인데 값이 하나뿐이라 모터 보드만 죽었을 때
        // 화면이 그것을 말할 방법이 없었다. 같은 행에서 오므로 mcuTime 을 함께 쓴다.
        Boolean motorLinkOk,
        // 녹화기 상태 (S15P11A301-310). mcuTime 과 같은 행에서 온다 — 신선도도 그것을 쓴다.
        // 두 필드는 독립이며 합치지 않는다: recorderOk=true 와 사유가 함께 오는 것이
        // 「지금은 정상이지만 이번 기동에 실패가 있었다」는 정상 조합이다.
        Boolean recorderOk,
        String recorderLastFailure,
        Instant poseTime,
        Double linearVelocity,
        Double angularVelocity) {
}

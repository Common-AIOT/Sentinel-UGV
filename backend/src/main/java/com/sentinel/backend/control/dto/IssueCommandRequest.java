package com.sentinel.backend.control.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * 임무 제어 명령 요청 (명세 27.4).
 *
 * <p>{@code MANUAL}/{@code AUTO} 는 모드 전환이며 {@code RESUME} 과 별개다
 * (S15P11A301-298). {@code AUTO} 는 수동 권한을 회수해 PAUSED 로 되돌릴 뿐이고
 * 주행을 재개하지 않는다(14.2, SR-008) — 다시 움직이려면 이어서 {@code RESUME} 을
 * 보내야 한다. 하나로 합치면 「자율로 되돌리기」와 「다시 움직이기」를 구분할 수 없다.
 *
 * <p>이 정규식은 {@code common/schemas/mission-command.schema.json} 의 type enum 과
 * 같아야 한다. 어긋나면 서버가 받아들인 명령을 젯슨이 계약 위반으로 버린다.
 */
public record IssueCommandRequest(
        @NotBlank @Pattern(regexp = "START|PAUSE|RESUME|RETURN|STOP|MANUAL|AUTO") String type
) {
}

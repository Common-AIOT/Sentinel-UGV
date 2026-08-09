// 모터 ESP32 전용 직렬 프레이밍 (S15P11A301-321).
//
// 센서 ESP32는 여전히 `jetson-comm`(COBS+CRC16+길이+uptime, protocol.h)을 그대로
// 쓴다 - 잘 동작 중이라 건드리지 않는다. 이 파일은 모터 링크만을 위한 완전히
// 별도의, 훨씬 단순한 프레이밍이다:
//
//   - 델리미터/이스케이프(COBS) 대신 **고정 길이 + 동기 워드**를 쓴다. 모든
//     메시지 타입의 payload 길이가 프로토콜상 이미 고정이므로(§34-5), 전송하는
//     길이 필드가 필요 없다 - 양쪽이 타입→길이 대응을 정적으로 안다.
//   - CRC16/COBS 대신 **CRC-8 한 바이트**. 프레임이 27바이트로 고정이라 동기
//     워드 2바이트 자체가 오탐 방지의 절반을 담당하고(1/65536), CRC-8까지
//     더하면 실질 오탐률은 COBS+CRC16 조합과 실용적으로 다르지 않다.
//   - 프레임 유실/재동기는 **1바이트씩 밀어보는 슬라이딩 윈도우**로 처리한다
//     (comm_task.cpp의 RX 루프). 델리미터를 찾는 로직이 없어 COBS보다 상태가
//     단순하다 - "27바이트 윈도우가 동기+CRC를 통과하는가"만 반복해서 묻는다.
//   - protocolVersion·payloadLength·senderUptimeMs 헤더 필드를 없앴다.
//     protocolVersion은 HELLO_ACK payload의 기존 필드(`HelloAck.protocolVersion`)
//     로 옮겨 그대로 유지한다(호환성 확인 용도). senderUptimeMs 제거로 Jetson
//     쪽 재부팅 감지(`RebootDetector`)는 모터 링크에서 더 이상 쓰지 않는다 -
//     대신 이 파일이 추가하는 `linkSilenceMs`(아래)가 링크 단절 자체를 훨씬
//     직접적으로 드러낸다.
//
// ## 왜 새로 만들었나
//
// S15P11A301-321 조사에서 실측된 무응답 보드는 `rx_frame_count=0` - 파싱 실패가
// 아니라 **바이트 자체가 안 왔다.** 기존 COBS+CRC16 프레이밍이 원인은 아니지만,
// 이 사건으로 두 가지가 드러났다:
//   1) 모터 링크에는 센서 링크의 150ms keepalive(`checkCommWatchdog`,
//      `markJetsonContact`) 같은 "링크 자체가 살아있는가"를 재는 장치가 없었다.
//      기존 300ms 워치독(`mode_arbiter.cpp`의 `jetsonStale`)은 DRIVE_COMMAND
//      **수신 빈도**만 보므로, "링크는 멀쩡한데 상위 파이프라인이 잠깐
//      DRIVE_COMMAND를 안 쏜다"와 "링크 자체가 죽었다"가 똑같이 STOPPING으로
//      보였다.
//   2) 프레이밍이 COBS+CRC16+길이+uptime으로 센서와 동일하게 무겁고, 모터
//      쪽에만 있는 문제를 진단하려면 매번 그 전체를 다시 읽어야 했다.
//
// 이 재작성은 (2)를 단순화하고, `comm_task.cpp`에 링크-접촉 워치독(§comm_task.cpp
// 참고, `lastValidJetsonRxMs`)을 추가해 (1)을 없앤다. **`mode_arbiter.cpp`의
// DRIVE_COMMAND 기반 안전 정지 로직은 그대로 둔다** - 그건 옳은 동작이다(상위
// 파이프라인이 멈추면 바퀴도 서야 한다). 새로 추가하는 것은 순수 진단용 축
// 하나: `MotorDiagnostic.linkSilenceMs`.
//
// `Arduino.h`에 의존하지 않아 호스트 g++로 빌드/시험할 수 있다
// (`test/test_motor_protocol.cpp` 참고, jetson-comm과 같은 패턴).
#pragma once

#include <cstddef>
#include <cstdint>

#include <message_ids.h>  // MessageId(MSG_HELLO 등), BoardRole, SetModeRequest, ConfigOperation, CommandAckResult
#include <protocol.h>      // DriveCommand/SetMode/DriveState/HelloAck/CommandAck/ConfigMessage pack/unpack, readU*/writeU* 헬퍼

// ---- 프레임 상수 ----
constexpr uint8_t MOTOR_SYNC0 = 0xA5;
constexpr uint8_t MOTOR_SYNC1 = 0x5A;

// DIAGNOSTIC(22바이트, linkSilenceMs 포함)이 모터가 쓰는 메시지 중 가장 크다.
// 그보다 작은 메시지는 남는 자리를 0으로 채워 보낸다.
constexpr size_t MOTOR_PAYLOAD_BYTES = 22;

// sync(2) + type(1) + sequence(1) + payload(22) + crc8(1)
constexpr size_t MOTOR_FRAME_BYTES = 2 + 1 + 1 + MOTOR_PAYLOAD_BYTES + 1;

// HELLO_ACK.protocolVersion에 실어 보내는 값. message_ids.h의 PROTOCOL_VERSION(센서가
// 쓰는 옛 프레이밍 버전)과는 별개 값공간이다 - 프레이밍을 통째로 바꿨으니 번호도
// 새로 매겨 두 세대가 우연히 같은 값으로 핸드셰이크에 성공하는 일을 막는다.
constexpr uint8_t MOTOR_PROTOCOL_VERSION = 2;

// ---- CRC-8 (poly 0x07 = x^8+x^2+x+1, init 0x00, 반전 없음, xorout 없음) ----
// 손계산 검증 벡터(테스트에서 그대로 확인): crc8([0x01]) == 0x07, crc8([0x02]) == 0x0E.
uint8_t motorCrc8(const uint8_t* data, size_t len);

// RFC1982 스타일이지만 8비트 폭이다 - DRIVE_COMMAND가 50Hz라 128스텝(약 2.56초)
// 이상 벌어진 재정렬은 어차피 워치독이 먼저 트립한다.
bool isMotorSequenceNewer(uint8_t candidate, uint8_t last);

enum class MotorParseResult : uint8_t {
  OK,
  BAD_LENGTH,   // len != MOTOR_FRAME_BYTES, 또는 outPayload가 MOTOR_PAYLOAD_BYTES보다 작음
  BAD_SYNC,     // 동기 워드 불일치 - 슬라이딩 윈도우가 아직 정렬 전
  BAD_CRC,
};

// payload(payloadLen바이트, payloadLen <= MOTOR_PAYLOAD_BYTES)를 남는 자리 0으로
// 채워 고정 MOTOR_FRAME_BYTES 프레임을 outBuf에 쓴다. 실패(payloadLen 초과 또는
// outCap 부족) 시 0을 반환한다.
size_t buildMotorFrame(uint8_t messageType, uint8_t sequence,
                        const uint8_t* payload, size_t payloadLen,
                        uint8_t* outBuf, size_t outCap);

// frame은 정확히 MOTOR_FRAME_BYTES 길이여야 한다(호출자가 슬라이딩 윈도우로 맞춘다).
// outPayload에는 항상 MOTOR_PAYLOAD_BYTES바이트가 쓰인다(패딩 포함) - 각
// unpackXxx() 호출 시 해당 메시지의 실제 고정 길이(예: DRIVE_COMMAND_BYTES)만
// 넘기면 패딩은 조용히 무시된다.
MotorParseResult parseMotorFrame(const uint8_t* frame, size_t len,
                                  uint8_t& outMessageType, uint8_t& outSequence,
                                  uint8_t* outPayload, size_t payloadCap);

// ==== DRIVE_STATE(0x20) 권한 확장 - 모터 전용 (S15P11A301-345) ====
//
// `protocol.h` 의 `DriveState`(15바이트) **뒤에 1바이트를 덧붙인다.** 필드를 끼워
// 넣지 않는 것이 핵심이다 - 모터 프레임은 payload 를 22바이트로 0 패딩해 보내고
// 젯슨 디코더는 자기가 아는 접두 바이트만 읽으므로(`motor_packet_codec.py` 상단
// 주석), 뒤에 붙인 바이트는 **구 디코더에 무해하고 신 디코더에만 보인다.**
//
// `faultFlags` 에 비트를 넣지 않은 이유: §34-9 의 16비트가 이미 전부 할당돼
// 있고(bit 15 는 미구현일 뿐 예약이 아니다), 애초에 이것은 결함이 아니라 **권한의
// 출처**다. 결함 비트에 섞으면 관제 화면에서 「고장」으로 읽힌다.
//
// `protocol.h` 를 고치지 않은 이유: 그 헤더는 센서 보드와 공유하는 COBS 링크의
// 것이고 `DRIVE_STATE_BYTES` 는 그쪽 길이 검증 테이블에도 쓰인다. 모터 링크만의
// 사정을 공유 헤더로 밀어 올리면 센서 채널까지 함께 흔들린다 - `MotorDiagnostic`
// 을 여기 따로 정의한 것과 같은 이유다.
constexpr size_t MOTOR_DRIVE_STATE_BYTES = DRIVE_STATE_BYTES + 1;  // 15 + authorityFlags

// authorityFlags bit 0. 「지금 수동 권한이 관제 승인이 아니라 링크 침묵 폴백으로
// 잡혀 있다」는 뜻이며, **래치**다(`MotorSharedState::manualFallbackLatched`).
// 발동은 젯슨이 침묵하는 동안 일어나므로 순간 플래그로 보내면 그 프레임을 아무도
// 받지 못한다. 내려가는 것은 `SET_MODE(AUTO)` 수락 하나뿐이다.
constexpr uint8_t AUTHORITY_FLAG_MANUAL_FALLBACK = 1u << 0;

size_t packMotorDriveState(const DriveState& in, uint8_t authorityFlags, uint8_t* out);
bool unpackMotorDriveState(const uint8_t* payload, size_t len, DriveState& out,
                            uint8_t& outAuthorityFlags);

// ==== DIAGNOSTIC(0x21) - 모터 전용, protocol.h의 Diagnostic/DIAGNOSTIC_BYTES와 다르다 ====
//
// 기존 4개 카운터에 linkSilenceMs 하나를 더했다. comm_task.cpp가 HELLO를 포함해
// Jetson으로부터 온 **어떤** 유효 프레임이든 받을 때마다 0으로 리셋한다(§comm_task.cpp
// markJetsonContact 참고). mode_arbiter가 올리는 FAULT_COMM_TIMEOUT_MOTOR(오직
// DRIVE_COMMAND 수신 빈도만 봄)와 이 값을 함께 읽으면:
//   - linkSilenceMs 작음 + fault 섬: 링크는 살아있다, 상위가 DRIVE_COMMAND를 안 보낸다
//   - linkSilenceMs 큼(수백ms 이상) + fault 섬: 링크 자체가 죽었다 (S15P11A301-321
//     실측 사례가 이쪽)
struct MotorDiagnostic {
  uint8_t boardRole;
  uint8_t boardState;
  uint16_t faultFlags;
  uint32_t crcErrorCount;
  uint32_t droppedFrameCount;
  uint32_t staleSequenceCount;
  uint32_t freeHeapBytes;
  uint16_t linkSilenceMs;
};
constexpr size_t MOTOR_DIAGNOSTIC_BYTES = 22;
size_t packMotorDiagnostic(const MotorDiagnostic& in, uint8_t* out);
bool unpackMotorDiagnostic(const uint8_t* payload, size_t len, MotorDiagnostic& out);

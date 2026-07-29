# GMS 장애 대응 및 관제 전송 대기

> Jira: S15P11A301-115  
> 적용 범위: VISION 트리거 이후 신규 음성 세션, STT 완료 후 GMS 호출, 관제 보고 인계
>
> 용어: [`음성-파이프라인-용어집.md`](음성-파이프라인-용어집.md)

## 1. 결정 사항

- 신규 음성 세션은 일반 인터넷 사이트가 아니라 **실제 GMS 호스트의 TCP 도달성**을
  먼저 확인한다.
- GMS 키가 없거나 GMS 호스트에 도달할 수 없으면 녹음·VAD·STT를 시작하지 않는다.
- TCP 연결 성공은 API 인증 성공을 뜻하지 않는다. 키 오류는 실제 API 호출의
  HTTP 401/403 응답으로 구분한다.
- STT가 완료된 뒤 GMS 호출이 실패하면 원인을 분류하고, 일시 장애만 한 번 재시도한다.
- 재시도 후에도 실패하면 33-8 키워드 파서로 축소 보고한다.
- Outbox 적재 또는 bridge 인계는 **관제 수신 성공이 아니다**. 관제 ACK가 오기 전까지
  성공 안내 음성을 재생하지 않는다.

## 2. 세션 시작 전 네트워크 확인

`session_gate.check_session_gate()`가 다음 순서로 검사한다.

1. `GMS_KEY` 설정 여부
2. `SENTINEL_GMS_BASE` URL의 호스트와 포트
3. 해당 호스트에 제한 시간 내 TCP 연결 가능 여부

기본 제한 시간은 2초다. 검사 과정에서는 GMS API 요청을 보내지 않아 크레딧을
소비하지 않는다. 마이크 실행 경로에서는 이 검사를 녹음보다 먼저 수행하며,
차단 결과이면 오디오 장치를 열지 않는다.

| 결과 | 신규 STT 세션 | 안내 |
|---|---:|---|
| `READY` | 시작 | 없음 |
| `GMS_MISCONFIGURED` | 차단 | `NETWORK_WAIT` |
| `GMS_UNAVAILABLE` | 차단 | `NETWORK_WAIT` |

이 검사는 “GMS 서버까지 통신 경로가 있는가”만 판단한다. API 키 유효성, 모델 사용
권한, 할당량은 실제 호출 결과에서 판정한다.

## 3. STT 완료 후 GMS 장애 분류

| 분류 | 대표 조건 | 재시도 | 최종 처리 |
|---|---|---:|---|
| `DEPENDENCY` | `openai` SDK 등 실행 의존성 누락 | 안 함 | 배포 환경 수정 |
| `NETWORK` | DNS·연결 거부·연결 손실 | 1회 | 33-8 폴백 |
| `TIMEOUT` | 호출 제한 시간 초과 | 1회 | 33-8 폴백 |
| `AUTH` | 키 누락, HTTP 401/403 | 안 함 | 33-8 폴백·운영자 확인 |
| `RATE_LIMIT` | HTTP 429 | 1회 | 33-8 폴백 |
| `SERVER` | HTTP 5xx | 1회 | 33-8 폴백 |
| `INVALID_RESPONSE` | JSON 파싱·응답 계약 오류 | 안 함 | 33-8 폴백 |
| `CLIENT` | 그 밖의 4xx·호출 오류 | 안 함 | 33-8 폴백 |

기본 호출 횟수는 최초 1회와 재시도 1회를 합친 최대 2회다. 재시도 간격은 기본
0.5초다. 인증·계약 오류를 반복 호출하지 않아 불필요한 지연과 크레딧 소비를 막는다.

운영 로그에는 분류, 예외 종류, 시도 횟수만 남긴다. GMS 키, 인증 헤더, 요구조자
발화 원문은 장애 메시지에 포함하지 않는다.

## 4. 관제 전송 대기 상태

`report_delivery.queue_report()`는 음성 모듈과 `sentinel_bridge` 사이의 경계다.
현재 Jira 128의 SQLite Outbox는 저장소 뼈대이며 실제 이벤트 적재는 팀원의
Jira 123 범위이므로, 음성 모듈이 해당 구현을 직접 수정하거나 가져오지 않는다.

| 상태 | 의미 | 허용 안내 |
|---|---|---|
| `PENDING` | 전송 어댑터가 아직 연결되지 않음 | `REPORT_PENDING` |
| `QUEUED` | bridge 또는 Outbox가 보고서를 인수함 | `REPORT_PENDING` |
| `FAILED` | 대기열 인계 실패 | `NETWORK_WAIT` |
| `SUCCEEDED` | 관제 ACK 확인 | S15P11A301-116에서 연결 |

`QUEUED`는 로컬 저장 성공일 뿐이다. “구조 요청이 관제에 전달되었습니다”는
S15P11A301-116에서 관제 ACK를 받은 경우에만 재생한다.

## 5. 환경 변수

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `SENTINEL_GMS_MAX_ATTEMPTS` | `2` | 최초 호출을 포함한 최대 GMS 호출 횟수 |
| `SENTINEL_GMS_RETRY_DELAY` | `0.5` | 재시도 전 대기 시간(초) |
| `SENTINEL_GMS_PROBE_TIMEOUT` | `2` | 세션 시작 전 GMS TCP 확인 제한 시간(초) |

## 6. 후속 범위

- S15P11A301-116: 관제 ACK·Mission Manager 탐사 재개 승인과 안내 음성 연결
- S15P11A301-123: `sentinel_bridge` 실제 이벤트 적재·재전송
- 실장 통합: Wi-Fi 차단·복구, 중복 전송, BRIO 100·Bluetooth 스피커 검증

## 7. 로컬 검증 절차

Miniforge Prompt에서 프로젝트의 `ai/stt`로 이동한다.

```bat
cd C:\Users\SSAFY\Desktop\S15P11A301\ai\stt
conda activate sentinel-audio
```

먼저 실제 API 호출 없이 GMS 호스트와 장애 정책을 검사한다.

```bat
python -m tools.check_gms_resilience
```

실제 호출은 `ai/stt/.env`에 키를 설정한 뒤 명시적으로 `--live`를 붙인다.

```bat
python -m tools.check_gms_resilience --live --report results\gms-smoke.json
```

실호출에는 개인정보가 없는 다음 고정 문장만 사용한다.

> 주변에 세 명이 있고 저는 움직일 수 없어요. 숨쉬기가 어렵습니다.

출력과 JSON에는 키·인증 헤더를 저장하지 않는다. `passed=true`,
`live.source=GMS`, `live.schemaValid=true`인지 확인한다.

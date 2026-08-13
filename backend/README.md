# Backend

Spring Boot 4.1·Java 21 기반 관제 API입니다. MQTT로 로봇의 presence·state·telemetry·event·ack를 받아 PostgreSQL/TimescaleDB에 저장하고, REST와 STOMP/WebSocket으로 관제 웹에 제공합니다. 미디어와 지도 파일은 S3 호환 스토리지에 직접 업로드할 수 있도록 presigned URL을 발급합니다.

## 로컬 실행

필수 환경은 JDK 21과 Docker Compose입니다.

```bash
cp backend/.env.example backend/.env.local
docker compose -f backend/compose.local.yaml up -d
cd backend
./gradlew bootRun
```

Windows PowerShell에서는 `cp` 대신 `Copy-Item`을 사용하고 Gradle 명령은
`./gradlew.bat bootRun`으로 실행합니다. 로컬 Compose는 TimescaleDB(PostgreSQL 15)와
Mosquitto만 띄웁니다. 미디어·지도 업로드를 시험하려면
`backend/.env.local`의 `S3_*`에 맞는 S3 호환 스토리지를 별도로 준비해야 합니다.

기동 후 확인 주소:

- API 문서: `http://localhost:8080/swagger-ui/index.html`
- OpenAPI JSON: `http://localhost:8080/v3/api-docs`
- 상태 확인: `http://localhost:8080/actuator/health`

## 외부 인터페이스

| 구분 | 현재 경로 |
|---|---|
| 임무 | `/api/v1/missions`, `/{missionId}`, `/trajectory`, `/telemetry` |
| 명령 | `/api/v1/missions/{missionId}/commands` |
| 요구조자 발견 | `/api/v1/missions/{missionId}/encounters`, `/api/v1/encounters/{encounterId}` |
| 최신 텔레메트리 | `/api/v1/telemetry/latest?robotId=...` |
| 제어 세션 | `/api/v1/control-sessions` |
| 미디어 | `/api/v1/media/uploads`, `/complete`, `/{mediaId}/view-url` |
| 지도 | `/api/v1/maps/uploads`, `/complete`, `/api/v1/missions/{missionId}/map` |
| 실시간 알림 | `/ws`에 STOMP 연결, `/topic/missions/{missionId}/events`, `/encounters` 구독 |

응답은 `ApiResponse { data, message, status }` 봉투를 사용합니다. 전체 요청·응답 계약은
Swagger와 [통합 명세 27·31장](../docs/05-통신-서버-영상.md)을 기준으로 합니다.

## 계층 이름 규칙

패키지는 도메인별(`control`, `encounter`, `media`, `mission`, `robot`, `telemetry`)로
나누고, 그 안의 클래스 이름이 **입력이 어디서 오는지**를 나타냅니다.

| 접미사 | 입력 | 부르는 쪽 |
|---|---|---|
| `~Controller` | HTTP·STOMP 요청 | 브라우저·젯슨 |
| `~Service` | 컨트롤러가 넘긴 요청 | `~Controller` |
| `~Writer` | MQTT 로 올라온 로봇 보고 | `MqttGateway` **단독** |

`~Service`와 `~Writer`는 **같은 계층**입니다. 스테레오타입도 둘 다 `@Service`이고
`JdbcTemplate`을 직접 씁니다. 이름을 나눈 것은 계층이 달라서가 아니라 **경로가 다르기
때문**입니다 — Writer 는 사람이 기다리는 요청이 아니라 초당 수 건씩 올라오는 보고를
받으므로, 실패했을 때 응답으로 알릴 상대가 없고 재시도·멱등이 그쪽 관심사입니다.

규칙은 지켜지고 있습니다. Writer 6개(`TelemetryWriter`, `RobotStateWriter`,
`RobotPresenceWriter`, `EncounterWriter`, `InteractionReportWriter`, `CommandAckWriter`)는
전부 `MqttGateway` 에만 주입되고, 컨트롤러에서 부르는 것은 하나도 없습니다. 같은 도메인의
조회는 `~QueryService` 로 갈라 둡니다(`EncounterQueryService`, `TelemetryQueryService`).

**새 클래스를 만들 때는 이 표를 먼저 봅니다.** MQTT 수신을 `~Service` 로 만들면 규칙이
조용히 무너지고, 그러면 어느 쪽이 사용자 응답 경로인지 이름으로 알 수 없게 됩니다.

## 설정

애플리케이션은 저장소 루트의 `.env`를 읽지 않습니다. 로컬 실행에서는
`backend/.env.local`을 읽고, 운영 Compose에서는 `backend/.env` 또는 배포 환경의 값을
컨테이너 환경변수로 주입합니다. 정확한 변수 목록은 [`.env.example`](.env.example)을
사용합니다.

현재 `/api/**`, `/actuator/**`, `/ws/**`, Swagger 경로에는 애플리케이션 인증이 없습니다.
Control Session은 동시 제어권을 조정하는 기능이지 사용자 인증이 아닙니다. 운영 경계는
보안 그룹·프록시·CORS에 의존하므로 공개 배포 시 [36장 보안 정책](../docs/06-테스트-보안-운영.md)을 확인합니다.

## 테스트와 데이터베이스 변경

```bash
cd backend
./gradlew test
```

스키마는 `src/main/resources/db/migration/`의 Flyway SQL만으로 변경합니다.

**JPA는 쓰지 않습니다.** `@Entity`도 `JpaRepository`도 없고 DB 접근은 전부
`JdbcTemplate`입니다. TimescaleDB hypertable과 그 위의 시계열 질의를 ORM으로 감싸지
않기로 한 결정입니다. 따라서 스키마와 코드를 이어 주는 자동 검증(`ddl-auto: validate`)이
없습니다 — **migration과 SQL 문자열이 어긋나면 기동이 아니라 그 질의를 부를 때 깨집니다.**
컬럼을 바꿀 때는 해당 SQL을 직접 찾아 함께 고치고, `./gradlew test`로 확인합니다.

## 배포 이미지와 롤백

CI는 커밋 SHA 태그(`<Docker Hub 계정>/spring-backend:<short-sha>`)로 이미지를 푸시하고,
`deploy:ec2`가 EC2의 `~/deploy/.env`에 `DOCKER_IMAGE=` 값을 고정합니다.

롤백은 EC2의 `~/deploy/.env`에서 `DOCKER_IMAGE`를 이전 SHA 태그로 바꾼 뒤 다음 명령으로
해당 이미지를 다시 올립니다.

```bash
docker compose -f compose.prod.yaml up -d
```

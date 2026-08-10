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
JPA의 `ddl-auto`는 `validate`이므로 엔티티만 바꾸고 migration을 누락하면 기동이 실패합니다.

## 배포 이미지와 롤백

CI는 커밋 SHA 태그(`<Docker Hub 계정>/spring-backend:<short-sha>`)로 이미지를 푸시하고,
`deploy:ec2`가 EC2의 `~/deploy/.env`에 `DOCKER_IMAGE=` 값을 고정합니다.

롤백은 EC2의 `~/deploy/.env`에서 `DOCKER_IMAGE`를 이전 SHA 태그로 바꾼 뒤 다음 명령으로
해당 이미지를 다시 올립니다.

```bash
docker compose -f compose.prod.yaml up -d
```

# Backend

Spring Boot 기반 관제 API입니다. 임무, 로봇 상태, 텔레메트리, 사람 탐지 이벤트, 제어권과 S3 업로드 메타데이터를 관리합니다.

초기 애플리케이션 생성 시 다음 경계를 유지합니다.

- `src/`: 애플리케이션과 테스트 소스
- `db/migration/`: Flyway SQL migration
- `tests/`: API·통합·계약 테스트 자료

DB 자격 증명과 AWS 키는 환경 변수 또는 GitLab CI 변수로만 주입합니다.

# Deployment

서버 런타임과 외부 노출 계층의 설정을 관리합니다.

- `ec2/`: Docker Compose와 환경 변수 예시
- `nginx/`: TLS 종료와 API/WebSocket reverse proxy
- `mediamtx/`: 로컬·원격 WebRTC 중계 설정

운영 시크릿은 GitLab CI 변수 또는 EC2의 권한 제한된 `.env` 파일로 주입합니다.

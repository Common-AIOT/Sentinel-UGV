# Scripts

반복 가능한 설치·배포·상태 점검·백업 명령을 보관합니다.

- `setup_jetson.sh`: Jetson 필수 도구 사전 점검
- `deploy_jetson.sh`: E-Stop 확인 후 ROS 2 빌드·테스트
- `health_check.sh`: 관제 API 상태 점검
- `backup.sh`: PostgreSQL 논리 백업

운영 적용 전 각 스크립트의 `--help`를 확인하고, 장치별 값은 환경 변수로 전달합니다.

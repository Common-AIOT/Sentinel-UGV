# Local Infrastructure

Host에서 Backend와 Frontend를 실행할 때 필요한 PostgreSQL/TimescaleDB와 MediaMTX만 Docker Compose로 제공합니다. EC2 배포용 [`../ec2/docker-compose.yml`](../ec2/docker-compose.yml)과 달리 PostgreSQL을 `localhost`에 노출하므로 개발 PC에서만 사용합니다.

```bash
cp .env.example .env
docker compose --env-file .env -f deploy/local/docker-compose.yml up -d
docker compose --env-file .env -f deploy/local/docker-compose.yml ps
```

종료:

```bash
docker compose --env-file .env -f deploy/local/docker-compose.yml down
```

데이터 volume은 기본적으로 유지됩니다. 테스트 데이터를 포함한 volume 삭제는 복구가 필요한 데이터가 없는지 확인한 뒤 개발자가 명시적으로 수행합니다.

운영 또는 팀 공유 서버에서는 이 Compose 파일로 DB를 노출하지 않습니다.

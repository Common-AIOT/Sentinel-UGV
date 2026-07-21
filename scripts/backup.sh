#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: BACKUP_DIR=/secure/path DB_CONTAINER=sentinel-ugv-postgres-1 ./scripts/backup.sh"
  echo "Creates a timestamped PostgreSQL custom-format dump."
  exit 0
fi

backup_dir="${BACKUP_DIR:-}"
db_container="${DB_CONTAINER:-}"
db_name="${POSTGRES_DB:-sentinel}"
db_user="${DB_USER:-sentinel}"

if [[ -z "${backup_dir}" || -z "${db_container}" ]]; then
  echo "BACKUP_DIR and DB_CONTAINER are required." >&2
  exit 1
fi

mkdir -p "${backup_dir}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_file="${backup_dir}/sentinel-${timestamp}.dump"

docker exec "${db_container}" pg_dump --format=custom --username "${db_user}" "${db_name}" >"${output_file}"
echo "Backup created: ${output_file}"

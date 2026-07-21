#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repository_dir=$(CDPATH='' cd -- "${script_dir}/.." && pwd)
failures=0

check_required_command() {
  command_name=$1
  if command -v "${command_name}" >/dev/null 2>&1; then
    echo "[ok] ${command_name}"
  else
    echo "[missing] required command: ${command_name}" >&2
    failures=$((failures + 1))
  fi
}

check_optional_command() {
  command_name=$1
  if command -v "${command_name}" >/dev/null 2>&1; then
    echo "[optional:ok] ${command_name}"
  else
    echo "[optional:skip] ${command_name}"
  fi
}

echo "Checking required host tools..."
check_required_command git
check_required_command docker

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    echo "[ok] Docker Engine"
  else
    echo "[unavailable] Docker Engine is not running or is not accessible." >&2
    failures=$((failures + 1))
  fi

  if docker compose version >/dev/null 2>&1; then
    echo "[ok] Docker Compose"
  else
    echo "[missing] Docker Compose plugin" >&2
    failures=$((failures + 1))
  fi
fi

echo "Checking module-specific tools (informational until each app is scaffolded)..."
for command_name in java node npm python3 colcon ros2; do
  check_optional_command "${command_name}"
done

if [ "${failures}" -eq 0 ]; then
  cd "${repository_dir}"
  docker compose \
    --env-file .env.example \
    --file deploy/local/docker-compose.yml \
    config --quiet
  echo "[ok] Local Docker Compose configuration"
  docker compose \
    --env-file deploy/ec2/.env.example \
    --file deploy/ec2/docker-compose.yml \
    config --quiet
  echo "[ok] EC2 Docker Compose configuration"
  echo "Development environment check passed."
  exit 0
fi

echo "Development environment check failed with ${failures} required issue(s)." >&2
exit 1

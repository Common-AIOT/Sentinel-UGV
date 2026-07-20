#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: API_HEALTH_URL=https://sentinel.example.com/health ./scripts/health_check.sh"
  exit 0
fi

health_url="${API_HEALTH_URL:-}"

if [[ -z "${health_url}" ]]; then
  echo "API_HEALTH_URL is required." >&2
  exit 1
fi

curl --fail --silent --show-error --max-time "${HEALTH_TIMEOUT_SECONDS:-10}" "${health_url}"
echo
echo "Health check passed: ${health_url}"

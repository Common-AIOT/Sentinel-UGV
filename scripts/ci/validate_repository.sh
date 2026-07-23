#!/usr/bin/env sh
set -eu

required_directories="
jetson/ros2_ws/src
backend/src
frontend/app
common/protocol
common/schemas
common/samples
docs
"

required_files="
.gitlab-ci.yml
backend/compose.local.yaml
backend/compose.prod.yaml
docs/Sentinel_UGV_최종_통합_명세서_v1.0-rc3.md
scripts/health_check.sh
scripts/check_dev_environment.sh
scripts/check_dev_environment.ps1
"

echo "Pipeline=${CI_PIPELINE_ID:-unknown} Job=${CI_JOB_ID:-unknown} Commit=${CI_COMMIT_SHA:-unknown} Runner=${CI_RUNNER_DESCRIPTION:-unknown}"

missing=0

for directory_path in $required_directories; do
  if [ ! -d "$directory_path" ]; then
    echo "Missing required directory: $directory_path" >&2
    missing=1
  fi
done

for file_path in $required_files; do
  if [ ! -s "$file_path" ]; then
    echo "Missing or empty required file: $file_path" >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "Repository structure validation failed." >&2
  exit 1
fi

echo "Repository structure validation passed."

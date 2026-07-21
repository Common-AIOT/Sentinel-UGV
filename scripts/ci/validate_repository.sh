#!/usr/bin/env sh
set -eu

required_directories="
jetson/ros2_ws/src
backend/src
frontend/app
common/protocol
common/schemas
common/samples
"

required_files="
.gitlab-ci.yml
deploy/ec2/docker-compose.yml
docs/repository-structure.md
scripts/health_check.sh
"

for directory_path in $required_directories; do
  if [ ! -d "$directory_path" ]; then
    echo "Missing required directory: $directory_path" >&2
    exit 1
  fi
done

for file_path in $required_files; do
  if [ ! -s "$file_path" ]; then
    echo "Missing or empty required file: $file_path" >&2
    exit 1
  fi
done

echo "Repository structure validation passed."

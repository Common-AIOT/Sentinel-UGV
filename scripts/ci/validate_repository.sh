#!/usr/bin/env sh
set -eu

required_directories="
jetson/ros2_ws/src
backend/src
frontend/app
common/protocol
common/schemas
common/samples
docs/architecture
docs/development
docs/testing
"

required_files="
.gitlab-ci.yml
deploy/ec2/docker-compose.yml
deploy/local/docker-compose.yml
deploy/local/README.md
docs/repository-structure.md
docs/architecture/system-context.md
docs/architecture/robot-runtime.md
docs/architecture/control-and-telemetry.md
docs/architecture/safety-policy.md
docs/development/local-setup.md
docs/testing/test-strategy.md
scripts/health_check.sh
scripts/check_dev_environment.sh
scripts/check_dev_environment.ps1
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

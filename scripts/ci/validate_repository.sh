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
docs/backend
docs/frontend
docs/hardware
docs/jetson
docs/operations
docs/product
docs/specifications
docs/testing
"

required_files="
.gitlab-ci.yml
deploy/ec2/docker-compose.yml
deploy/local/docker-compose.yml
deploy/local/README.md
docs/README.md
docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
docs/architecture/05-전체-시스템-아키텍처.md
docs/architecture/14-상태-머신-및-안전-정책.md
docs/architecture/31-Jetson-Spring-Boot-관제-웹-통신-설계.md
docs/testing/16-테스트-및-검증-계획.md
docs/testing/38-요구사항-추적표-최종-인수-시험.md
scripts/docs/split-integrated-spec.ps1
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

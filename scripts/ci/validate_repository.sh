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
docs/README.md
scripts/README.md
scripts/setup_jetson.sh
scripts/start_sentinel.sh
scripts/stop_sentinel.sh
scripts/gen_stream_cert.sh
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

# scripts/README.md가 실제 파일 목록과 어긋나는 일이 반복됐다. 스크립트를
# 추가하고 문서를 안 고치면 다음 사람이 그 스크립트를 모른다. CI가 잡는다.
for script_path in scripts/*.sh; do
  script_name=$(basename "$script_path")
  if ! grep -q "$script_name" scripts/README.md; then
    echo "Script not documented in scripts/README.md: $script_name" >&2
    missing=1
  fi
done

# 최상위에 colcon 산출물이 커밋되는 것을 막는다. 부분 빌드를 실수로
# source하면 옛 코드가 돌면서 원인을 찾기 어려운 증상이 생긴다.
for stray_path in install log; do
  if [ -e "$stray_path" ]; then
    echo "Stray colcon artifact at repository root: $stray_path" >&2
    echo "  Build inside jetson/ros2_ws, not at the root." >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo "Repository structure validation failed." >&2
  exit 1
fi

echo "Repository structure validation passed."

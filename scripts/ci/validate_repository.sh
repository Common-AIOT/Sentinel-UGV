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
scripts/demo_up.sh
scripts/demo_down.sh
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
#
# .py도 본다 (S15P11A301-379). 검사가 .sh만 볼 때 scripts/의 파이썬 도구 3개가
# 통째로 사각지대에 있었고, 실제로 telemetry_sim.py가 어느 문서에도 없는 채로
# 마감 감사(S15P11A301-378)까지 갔다. 남은 둘은 안전 체인을 우회해 게이트 아래로
# 직접 명령을 쏘는 계측 도구다 — 그런 것이 저장소에 있다는 사실을 README만 보는
# 사람이 모르는 쪽이 더 위험하다.
#
# 글로브가 아무것도 못 맞히면 셸은 패턴 문자열을 그대로 넘긴다. 그때 없는 파일을
# 미문서화로 신고하지 않도록 존재를 먼저 확인한다.
for script_path in scripts/*.sh scripts/*.py; do
  [ -e "$script_path" ] || continue
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

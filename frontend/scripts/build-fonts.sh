#!/usr/bin/env bash
#
# S15P11A301-302 — public/fonts 의 woff2 를 원본에서 다시 만든다.
#
# 결과물은 커밋되어 있으므로 평소에는 실행할 필요가 없다. 원본 글꼴을
# 올리거나 서브셋 범위를 바꿀 때만 돌린다.
#
#   pip install 'fonttools[woff]' brotli
#   frontend/scripts/build-fonts.sh
#
# 서브셋 범위를 이렇게 나눈 이유는 docs/frontend.md 의 "글꼴" 절에 있다.
# 요점만: D2Coding 에서 한글을 의도적으로 뺀다. 그래야 font-mono 스팬 안의
# 한글이 Asta Sans 로 폴백해서, "데이터 스트립만 고정폭" 규칙이 개발자
# 규율이 아니라 폰트 스택 자체로 강제된다.

set -euo pipefail

cd "$(dirname "$0")/.."
OUT="public/fonts"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$OUT"

ASTA_URL="https://github.com/42dot/Asta-Sans/raw/main/fonts/variable/AstaSans%5Bwght%5D.ttf"
D2_URL="https://cdn.jsdelivr.net/gh/joungkyun/font-d2coding@1.3.2"

# 라틴 + 기호. 한글보다 훨씬 작아서 먼저 그려진다.
ASTA_LATIN="U+0000-00FF,U+0100-024F,U+0300-036F,U+2000-206F,U+2070-209F,\
U+20A0-20BF,U+2100-214F,U+2190-21FF,U+2200-22FF,U+2460-24FF,U+2500-257F,\
U+25A0-25FF,U+2600-26FF,U+2700-27BF"

# 한글. Asta 는 조합형 자모(U+1100-11FF)와 한자가 없어서 선언하지 않는다 —
# 없는 범위를 선언하면 브라우저가 받아놓고 폴백하느라 헛돈다.
ASTA_KOREAN="U+3000-303F,U+3130-318F,U+AC00-D7A3,U+FF00-FFEF"

# D2Coding: 한글 범위 없음. 이 목록에 U+AC00-D7A3 을 추가하지 말 것.
#
# 기호 블록도 뺐다. 고정폭이 값을 갖는 건 자릿수가 맞아야 하는 ASCII 뿐이고,
# mono 스택은 "D2Coding" 다음이 "Asta Sans" 라 빠진 기호는 Asta 가 받는다.
# 블록을 통째로 넣으면 굵기당 60KB, 이렇게 하면 8KB 다.
# 예외로 U+26A0(경고)과 U+2715(닫기)는 명시한다 — Asta 에 이 둘이 없어서
# 여기서도 빼면 OS 폰트로 갈린다.
D2_RANGES="U+0000-00FF,U+2000-206F,U+2212,U+26A0,U+2715"

echo "원본 내려받는 중..."
curl -sfL -o "$WORK/AstaSans.ttf"     "$ASTA_URL"
curl -sfL -o "$WORK/D2Coding.ttf"     "$D2_URL/D2Coding.ttf"
curl -sfL -o "$WORK/D2CodingBold.ttf" "$D2_URL/D2CodingBold.ttf"

subset() { # <입력> <출력> <범위>
  pyftsubset "$1" --output-file="$2" --flavor=woff2 \
    --unicodes="$3" --layout-features+=tnum >/dev/null
}

echo "서브셋 만드는 중..."
subset "$WORK/AstaSans.ttf"     "$OUT/AstaSans-latin.woff2"   "$ASTA_LATIN"
subset "$WORK/AstaSans.ttf"     "$OUT/AstaSans-korean.woff2"  "$ASTA_KOREAN"
subset "$WORK/D2Coding.ttf"     "$OUT/D2Coding-mono.woff2"    "$D2_RANGES"
subset "$WORK/D2CodingBold.ttf" "$OUT/D2Coding-mono-bold.woff2" "$D2_RANGES"

echo
echo "완료. 결과:"
ls -l "$OUT"/*.woff2 | awk '{printf "  %-34s %8.1f KB\n", $9, $5/1024}'

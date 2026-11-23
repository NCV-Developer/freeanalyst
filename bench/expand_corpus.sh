#!/bin/bash
# A4 扩样：把基准语料从 10 家扩到 20 家 + 4 份早年報。
#
# 新增公司只用 --limit 1（不取英文版），减少重复和下载时间；
# 2021 年报放到独立目录，跑矩阵时用 --year 2021 单独跑，
# 免得和 2025 年报在公司级归并时混在一起。
set -u
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-/usr/local/bin/python3.12}
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"

NEW="000002 000651 001979 601398 601318 600030 600028 002594 832566 430047"
EARLY="600519 002572 300588 688316"

for c in $NEW; do
  echo "--- $c (2025)"
  "$PY" freeanalyst.py fetch "$c" --market cn --kind annual --period 2025 \
      --limit 1 --out "materials/corpus/$c" 2>&1 | tail -2
done

for c in $EARLY; do
  echo "--- $c (2021)"
  "$PY" freeanalyst.py fetch "$c" --market cn --kind annual --period 2021 \
      --limit 1 --out "materials/corpus2021/$c" 2>&1 | tail -2
done

echo "=== 语料份数 ==="
find materials/corpus -name "*.pdf" | wc -l
find materials/corpus2021 -name "*.pdf" 2>/dev/null | wc -l

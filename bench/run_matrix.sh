#!/bin/bash
# 跑取数覆盖矩阵 —— **解释器钉死**，否则基线不可比。
#
# 用法： bash bench/run_matrix.sh [输出名]
#   bash bench/run_matrix.sh baseline
#   → out/coverage-baseline.csv / .txt
set -u
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-/usr/local/bin/python3.12}
TAG=${1:-run}
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"

"$PY" -c "import pdfplumber" 2>/dev/null || {
  echo "✗ $PY 里没有 pdfplumber，矩阵跑出来全是『取不到』，不是真结果。" >&2
  exit 1
}

"$PY" freeanalyst.py coverage materials/corpus --year 2025 \
    --out "out/coverage-$TAG.csv" --max-bad 9999 > "out/coverage-$TAG.txt" 2>&1
echo "exit=$?"
head -8 "out/coverage-$TAG.txt"

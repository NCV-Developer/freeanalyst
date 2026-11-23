#!/bin/bash
# 重建基准语料 —— **放到固定位置**，不再用 /tmp。
#
# 为什么要有这个脚本：2026-10-27 系统把 /tmp 清了，36 份材料和基线数字一起没了。
# 度量依赖的材料必须是可重建的：同样的代码 → 同样的材料 → 可比的基线。
set -u
cd "$(dirname "$0")/.." || exit 1
# ⚠️ 解释器必须钉死：pdfplumber 只装在 3.12 的用户目录里，
# hermes venv 的 3.11 没有它 —— 用错解释器，PDF 全部解析失败，
# 而矩阵会把"解析失败"记成"取不到"，看起来像 bug，其实是环境错了。
PY=${PY:-/usr/local/bin/python3.12}
export HTTPS_PROXY="http://127.0.0.1:7897"
OUT="materials/corpus"
mkdir -p "$OUT"

# A 股 10 家（覆盖沪市/深市/科创板，含附注列与无附注列两种版式）
CN="600519 002572 301215 688316 688021 301072 300588 301220 688059 600930"
# 港股 2 家（一家银行、一家工业）
HK="00939 01810"

echo "=== A 股 ==="
for c in $CN; do
  echo "--- $c"
  "$PY" freeanalyst.py fetch "$c" --market cn --kind annual --period 2025 \
      --limit 2 --out "$OUT/$c" 2>&1 | tail -3
done

echo "=== 港股 ==="
for c in $HK; do
  echo "--- $c"
  "$PY" freeanalyst.py fetch "$c" --market hk --kind annual --period 2025 \
      --limit 2 --out "$OUT/$c" 2>&1 | tail -3
done

echo "=== 美股 ==="
for t in AAPL WMT; do
  echo "--- $t"
  "$PY" freeanalyst.py fetch "$t" --market us --kind "10-K 20-F" \
      --limit 1 --out "$OUT/$t" 2>&1 | tail -3
done

echo "=== 结果 ==="
find "$OUT" -type f \( -name "*.pdf" -o -name "*.htm" -o -name "*.html" \) | wc -l

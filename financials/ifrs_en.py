"""IFRS 英文报表抽取 —— 新加坡 / 港股英文年报共用。

## 与台湾 MOPS 的关系

台湾 MOPS 的科目本来就带英文标准名（`Total assets`、`Operating revenue`），
与 IFRS 英文年报是**同一套语言**。所以词表直接复用 `financials/tw.ITEM_MAP`，
这里只解决「PDF 文本 → 数值」这一段。

## ⚠️ 验证状态（2026-11-22）

**词表与机制已就绪，但尚未用真实新加坡年报验证。**
SGX 与公司 IR 在当前环境均为 JS 渲染或反爬，拿不到原始 PDF；
手上的「英文版 A 股年报」实测只是 8–22 页的摘要，不是完整年报。

**拿到一份真实 SG 年报前，不得宣称支持新加坡。**
"""

from __future__ import annotations

import re

from .tw import ITEM_MAP, NOT_APPLICABLE  # noqa: F401  （词表复用，别另起一份）

#: 标签与金额**可能不在同一行** —— 年报常见的两种排法：
#:   Total assets 7,933,023,878 6,691,938,000
#:   Total assets
#:     7,933,023,878
_NUM = r"\(?-?\d[\d,]*(?:\.\d+)?\)?"


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    v = float(s)
    return -v if neg else v


def extract_text(text: str) -> dict[str, float]:
    """从整份报表文本里取科目值（**本期列**，即标签后第一个数字）。"""
    out: dict[str, float] = {}
    lines = [l.strip() for l in text.splitlines()]

    for item, (pat, excl) in ITEM_MAP.items():
        for i, line in enumerate(lines):
            if not re.search(pat, line, re.IGNORECASE):
                continue
            if excl and re.search(excl, line, re.IGNORECASE):
                continue
            # 同一行里，标签**之后**的第一个数字
            m0 = re.search(pat, line, re.IGNORECASE)
            tail = line[m0.end():] if m0 else ""
            for cand in [tail] + lines[i + 1:i + 3]:       # 允许标签独占一行
                m = re.search(_NUM, cand)
                if m:
                    v = _num(m.group(0))
                    if v is not None:
                        out[item] = v
                        break
            if item in out:
                break
    return out


def extract_pdf(doc) -> dict[str, float]:
    """从 `ingest.pdf.extract_pdf()` 的文档里取（按页拼接，**只取首次出现**）。"""
    out: dict[str, float] = {}
    for pg in doc.pages:
        for k, v in extract_text(pg.raw_text or "").items():
            out.setdefault(k, v)
    return out

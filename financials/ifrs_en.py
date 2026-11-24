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


def _row_value(row, idx: int) -> float | None:
    """从一行里取**本期**值。

    实测 9R 年报的两种行形态：
        ['Revenue', '4', '13,636,473', '14,701,312']      → 跳过附注号 4
        ['Inventories 16 1,065,513', '692,117', '–']      → 本期挤在标签格里
    """
    # ① 标签格里就带数字（「科目 附注 本期」挤一格）→ 尾部那个数是本期
    head = str(row[0])
    m = re.search(r"(\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*$", head)
    if m:
        v = _num(m.group(1))
        if v is not None and abs(v) >= 1000:
            return v
    # ② 否则在后续列里找第一个「像金额」的，**跳过附注号**（小整数）
    for c in row[1:]:
        v = _num(str(c))
        if v is not None and (abs(v) >= 1000 or "," in str(c)):
            return v
    return None


def extract_pdf(doc) -> dict[str, float]:
    """从 PDF 里取（**按表格行 + 列角色**，不是"标签后第一个数字"）。

    实测（9R Limited / CRCT 真实年报）：按文本行取会把**页码和附注号**当金额 ——
    货币资金取出 31、存货取出 16、资产总计取出 24。所以必须走
    `from_pdf.page_rows()` 的表格行，并用 `ingest.columns` 判哪一列是本期。
    """
    from ingest import columns as _cols
    from . import from_pdf as _fp

    out: dict[str, float] = {}
    # **只在主表页取值。** 9R 实测：全文档「首次出现优先」会取到 MD&A 与附注里的
    # 同名科目 —— 营业成本取出 14,244,770（实际是别处的数），净利润取出正数
    # （而这家公司当年是亏损 -2,597,300）。主表标题页及其续页以外的行一律不碰。
    main_pages: set[int] = set()
    for pg in doc.pages:
        t = (pg.raw_text or "").upper()
        if any(k in t for k in (
                "STATEMENT OF FINANCIAL POSITION", "STATEMENTS OF FINANCIAL POSITION",
                "PROFIT OR LOSS", "INCOME STATEMENT", "BALANCE SHEET",
                "STATEMENT OF CASH FLOWS", "STATEMENTS OF CASH FLOWS",
                "STATEMENT OF COMPREHENSIVE INCOME")):
            main_pages.add(pg.number)
            main_pages.add(pg.number + 1)          # 主表常跨两页

    for pg in doc.pages:
        if pg.number not in main_pages:
            continue
        rows = _fp.page_rows(pg)
        if not rows:
            continue
        roles = _cols.detect(rows)
        idx = roles.primary if (roles.ok and roles.primary is not None) else 1
        for row in rows:
            if not row:
                continue
            label = str(row[0])
            for item, (pat, excl) in ITEM_MAP.items():
                if item in out:
                    continue
                if not re.search(pat, label, re.IGNORECASE):
                    continue
                if excl and re.search(excl, label, re.IGNORECASE):
                    continue
                v = _row_value(row, idx)
                if v is not None:
                    out[item] = v
    return out

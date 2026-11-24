"""台湾上市公司：MOPS 官方结构化财报 → 三张表关键科目。

## 为什么这条路比 PDF 好得多

MOPS（`mopsov.twse.com.tw`）返回的是**申报用的结构化 HTML**：
每个科目都带**中英双语标准名**（`現金及約當現金 Cash and cash equivalents`），
所以映射是**查表**，不是猜繁体字形。而且是官方申报数据，天然可核对。

## 单位

MOPS 财报单位是**新台币千元**，取出后要 ×1000 才是元。

## ⚠️ 台湾现金流量表是**间接法**

没有「销售商品、提供劳务收到的现金」「购买商品、接受劳务支付的现金」这两行 ——
这两个科目在台湾**不适用**，不是解析失败。港股/美股/新加坡同理。
"""

from __future__ import annotations

import re

#: 科目 → （匹配模式, 排除模式）。按顺序取第一个命中的行，**取本期那一列**。
ITEM_MAP: dict[str, tuple[str, str | None]] = {
    "货币资金": (r"Cash and cash equivalents", r"期末|期初|帳列"),
    "应收账款": (r"Accounts receivable|Notes and accounts receivable|Trade receivables", None),
    "存货": (r"Inventories", r"Adjustments|decrease|increase"),
    "流动资产合计": (r"Total current assets", None),
    "非流动资产合计": (r"Total non-current assets", None),
    "资产总计": (r"Total assets", None),
    "流动负债合计": (r"Total current liabilities", None),
    "负债合计": (r"Total liabilities", r"and equity"),
    "营业收入": (r"Total operating revenue|Operating revenue|^Revenue\b", None),
    "营业成本": (r"Total operating costs|Operating costs|Cost of sales", None),
    # 台湾写「營業利益 Net operating income (loss)」
    "营业利润": (r"Net operating income|Operating income|Operating profit|Profit from operations", None),
    "净利润": (r"Profit \(loss\)|Profit for the (financial )?year|Profit for the period|Net profit|Loss for the (year|period)|Loss for the financial year",
               r"continuing|before tax|attributable|comprehensive"),
    # 台湾写「營業活動之淨現金流入(流出) Net cash flows from (used in) operating activities」
    # —— 中间插了 "(used in)"，所以不能要求 from 后面紧跟 activities 类名
    "经营活动产生的现金流量净额": (r"Net cash .*?operating activities", None),
    "投资活动产生的现金流量净额": (r"Net cash .*?investing activities", None),
    "筹资活动产生的现金流量净额": (r"Net cash .*?financing activities", None),
    "期末现金及现金等价物余额": (r"期末現金及約當現金餘額|Cash and cash equivalents at (the )?end", None),
}

#: 台湾**不适用**的科目（间接法没有这两行）
NOT_APPLICABLE = ("销售商品、提供劳务收到的现金", "购买商品、接受劳务支付的现金")


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    v = float(s)
    return -v if neg else v


def extract(doc) -> dict[str, float]:
    """从 `ingest.html.extract_html()` 的结果里取科目值（**新台币千元**）。"""
    out: dict[str, float] = {}
    for item, (pat, excl) in ITEM_MAP.items():
        for t in doc.tables:
            for row in t.rows:
                cells = [str(c) for c in row.cells]
                # 标签的位置两种都有：有的解析器放进 `row.label`，
                # MOPS 实际结构里则留在 cells[0]。合起来匹配，取值时跳过非数值单元格。
                label = " ".join([str(row.label or "")] + cells)
                if not re.search(pat, label, re.IGNORECASE):
                    continue
                if excl and re.search(excl, label, re.IGNORECASE):
                    continue
                for c in cells:               # 跳过非数值单元格，第一个数值 = 本期
                    v = _num(c)
                    if v is not None:
                        out[item] = v
                        break
                if item in out:
                    break
            if item in out:
                break
    return out

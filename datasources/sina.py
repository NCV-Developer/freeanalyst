"""新浪的结构化三表 —— **只作校验器，不作数据来源**。

## 为什么它不能当来源

它给的是结构化数字，**没有页码、没有原始文档链接** —— 指不回原始披露。
而本项目的硬要求是「每个数字都要能追溯到哪份公告的哪一页」。
所以：

    ✅ 用它：本地抽完之后，逐项对一遍，差了就报警
    ❌ 用它：直接把它当数据填进模型

## 它能证明什么

2026-10-26 实测（某白酒 2025 年报）：把它当对照，查出两个 bug ——
附注号被当成金额（营业成本 44 vs 148.9 亿）、「营业总收入」被当成「营业收入」（差 32.2 亿）。
这两个错误**都不违反勾稽、不崩溃、数字形状完全正常**，只有逐项对账才看得见。

## 接口

    GET https://quotes.sina.cn/cn/api/openapi.php/
        CompanyFinanceService.getFinanceReport2022
        ?paperCode=sh600519&source=lrb&type=4&page=1&num=2

``type=4`` 只返回年报（未公开参数，实测有效）；``source`` 三张表：
``lrb`` 利润表 / ``fzb`` 资产负债表 / ``llb`` 现金流量表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import net

URL = ("https://quotes.sina.cn/cn/api/openapi.php/"
       "CompanyFinanceService.getFinanceReport2022")

#: 本项目内部叫法 → 新浪的 source 参数
SOURCES = {"income": "lrb", "balance": "fzb", "cash_flow": "llb"}

_HOST = frozenset({"quotes.sina.cn"})
_UA = "FreeAnalyst/0.60 (noreply@newchapterventures.com)"


def paper_code(code: str) -> str:
    """6 位代码 → 新浪要的带交易所前缀的代码。

    ⚠️ **不能按 60 开头就判沪市** —— 北交所（8/4/920 开头）也在同一套编号里。
    """
    c = str(code).strip()
    if not c.isdigit() or len(c) != 6:
        raise ValueError(f"不是 6 位 A 股代码：{code!r}")
    if c[0] in "48" or c.startswith("920"):
        return f"bj{c}"
    if c[0] == "6":
        return f"sh{c}"
    return f"sz{c}"


@dataclass
class SinaPeriod:
    """新浪给的一个报告期。"""

    date: str                       # 20251231
    scope: str = ""                 # 合并期末 / 母公司期末
    currency: str = ""
    audited: str = ""               # 是 / 未审计
    opinion: str = ""               # 标准无保留审计意见
    published: str = ""
    items: dict[str, float] = field(default_factory=dict)

    @property
    def year(self) -> int:
        return int(self.date[:4]) if self.date[:4].isdigit() else 0


def fetch(code: str, kind: str, periods: int = 2) -> list[SinaPeriod]:
    """取一个公司一张表最近 N 期（默认 2 期：本期 + 上期，用来自动识别「取错年份」）。

    **取不到就抛** —— 返回空列表跟「这家没这个表」长得一样。
    """
    if kind not in SOURCES:
        raise ValueError(f"kind 只能是 {list(SOURCES)}，收到 {kind!r}")
    paper = paper_code(code)
    q = net.PublicQuery(
        params={"paperCode": paper, "source": SOURCES[kind], "type": "4",
                "page": "1", "num": str(max(1, periods))},
        purpose=f"取 {code} 的{kind}（对账用）",
        extra_hosts=_HOST,
    )
    raw = net.guarded_get(URL, q, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        blob = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise LookupError(f"新浪返回的不是 JSON：{raw[:120]!r}") from exc

    data = (blob.get("result") or {}).get("data") or {}
    lst = data.get("report_list") or {}
    if not isinstance(lst, dict) or not lst:
        raise LookupError(
            f"新浪没有 {paper} 的 {SOURCES[kind]} 数据。"
            f"**这不等于「这家没这张表」** —— 可能只是它没收录。")

    out: list[SinaPeriod] = []
    for date, body in lst.items():
        items: dict[str, float] = {}
        for r in body.get("data") or []:
            v = r.get("item_value")
            if v in (None, "", "--"):
                continue
            try:
                items[str(r.get("item_title") or "")] = float(v)
            except (TypeError, ValueError):
                continue
        out.append(SinaPeriod(
            date=str(date), scope=str(body.get("rType") or ""),
            currency=str(body.get("rCurrency") or ""),
            audited=str(body.get("is_audit") or ""),
            opinion=str(body.get("audit_opinion") or ""),
            published=str(body.get("publish_date") or ""),
            items=items,
        ))
    out.sort(key=lambda p: p.date, reverse=True)
    return out

"""真值提供者 —— 对账用的**独立来源**，按市场分开。

## 什么是真值

一个**不是从同一份材料、用同一套理解方式**得出的正确答案。

    同一份 PDF 抽两遍           ✗ 不是真值（会犯同样的错）
    同一份材料的中英文两版       △ 弱真值（发现不了共同的理解错误）
    交易所 / 官方申报的数据      ✓ 真值

实测证明这条很要紧：附注号被当成金额那个 bug，同一份 PDF 抽两遍照样是 44，
只有拿新浪的结构化数据对照才露。所以**真值的独立性决定了对账的价值**。

## 为什么必须分级

不同来源的可信度不同，结论强度也不同。报告里必须标出来 ——
「用第三方数据判我们错了」和「用官方申报数据判我们错了」不是一回事。

## 没有真值的时候

返回空 —— 调用方把格子标成 `UNVERIFIED`。

**不许拿"自己跟自己对"充数**，也不许因为拿不到就跳过这一格不记。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class TruthGrade(Enum):
    OFFICIAL = "官方申报数据"          # SEC XBRL / TWSE
    VENDOR = "第三方结构化数据"        # 新浪
    SELF = "同一文档的另一版本"        # 中英文互校 —— 弱，发现不了共同错误


@dataclass
class Truth:
    """一份对照数据。"""

    market: str
    code: str
    year: int
    period: str
    grade: TruthGrade
    source: str
    #: {statement: {item: value}}，statement ∈ income / balance / cash_flow
    items: dict[str, dict[str, float]]


class Provider(Protocol):
    market: str

    def fetch(self, code: str, year: int | None) -> Truth | None:
        """取不到返回 None（调用方标 UNVERIFIED），**不要返回空 dict 假装有**。"""


class NoTruth:
    """没有独立真值的市场 —— 港股 / 台股 / 新加坡 / 私人企业。

    **它不是"还没做"，是"这类材料本来就没有第二来源"**（私人企业审计报告
    只有那一份 PDF）。标 UNVERIFIED 是事实，不是缺陷。
    """

    def __init__(self, market: str = ""):
        self.market = market

    def fetch(self, code: str, year: int | None) -> Truth | None:
        return None


class SinaTruth:
    """A 股：新浪结构化三表。第三方，但数据来自交易所电子申报，独立于 PDF。"""

    market = "cn"

    def fetch(self, code: str, year: int | None) -> Truth | None:
        from datasources import sina

        raw = {}
        period = ""
        for kind in ("income", "balance", "cash_flow"):
            try:
                periods = sina.fetch(code, kind, periods=2)
            except Exception:                      # noqa: BLE001
                # 一张表取不到不影响别的；但这一张的格子会落 UNVERIFIED
                continue
            if not periods:
                continue
            per = next((p for p in periods if year and p.year == year), periods[0])
            raw[kind] = dict(per.items)
            if not period:
                period = per.date
        if not raw:
            return None
        return Truth(
            market="cn", code=code, year=year or 0, period=period,
            grade=TruthGrade.VENDOR, source="新浪结构化三表",
            items=raw,
        )


def for_market(market: str) -> Provider:
    """这个市场有没有真值。**没有就返回 NoTruth，不返回 None。**"""
    if market == "cn":
        return SinaTruth()
    return NoTruth(market)

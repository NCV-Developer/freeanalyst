"""美股：SEC 官方 XBRL 接口取数（companyconcept）。

    https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json

## 为什么不解析 10-K 的 HTML

AAPL 的 10-K 是 Workiva 产的 inline XBRL（`ix:nonFraction`），
与 Fitbit 那种 `onclick=defref_us-gaap_...` 写法不同，标签抽取会落空。
**而 SEC 自己就把每个科目的官方值给了** —— 直接取，比解析 HTML 可靠得多，
还能顺便当对账的独立真值。

## 需要 UA

SEC 要求带邮箱的 User-Agent（`SEC_USER_AGENT` 环境变量）。
"""

from __future__ import annotations

import json
import os
import urllib.request

#: 科目 → XBRL concept 候选（按优先级，第一个有值的用）
CONCEPTS: dict[str, tuple[str, ...]] = {
    "货币资金": ("CashAndCashEquivalentsAtCarryingValue",),
    "应收账款": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "存货": ("InventoryNet",),
    "流动资产合计": ("AssetsCurrent",),
    "非流动资产合计": ("AssetsNoncurrent",),
    "资产总计": ("Assets",),
    "流动负债合计": ("LiabilitiesCurrent",),
    "负债合计": ("Liabilities",),
    "营业收入": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    "营业成本": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "营业利润": ("OperatingIncomeLoss",),
    "净利润": ("NetIncomeLoss",),
    "经营活动产生的现金流量净额": ("NetCashProvidedByUsedInOperatingActivities",),
    "投资活动产生的现金流量净额": ("NetCashProvidedByUsedInInvestingActivities",),
    "筹资活动产生的现金流量净额": ("NetCashProvidedByUsedInFinancingActivities",),
    # 期末现金与资产负债表货币资金是同一个 concept（口径差异见报告注释）
    "期末现金及现金等价物余额": ("CashAndCashEquivalentsAtCarryingValue",),
}

_UA = os.environ.get("SEC_USER_AGENT", "FreeAnalyst/0.60 (noreply@newchapterventures.com)")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def concept(cik: str, tag: str, *, unit: str = "USD") -> list[dict]:
    """取一个 concept 的全部申报值（按期末日倒序）。"""
    cik10 = str(cik).lstrip("0").zfill(10)
    j = _get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/us-gaap/{tag}.json")
    vals = (j.get("units") or {}).get(unit) or []
    return sorted(vals, key=lambda v: v.get("end", ""), reverse=True)


def annual_values(cik: str, year: int, *, forms: tuple[str, ...] = ("10-K",)) -> dict[str, float]:
    """取 16 个适用科目的**年度**值（form=10-K、期末在该年内、fp=FY）。

    返回 `{科目: 值}`（美元）。**取不到的科目不在 dict 里** —— 不填 0、不填默认值。
    """
    out: dict[str, float] = {}
    for item, tags in CONCEPTS.items():
        for tag in tags:
            try:
                vals = concept(cik, tag)
            except Exception:                            # noqa: BLE001
                continue
            hit = next((v for v in vals
                        if v.get("form") in forms
                        and str(v.get("end", "")).startswith(str(year))
                        and v.get("fp") == "FY"), None)
            if hit is not None and hit.get("val") is not None:
                out[item] = float(hit["val"])
                break
    return out

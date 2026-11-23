"""分层 —— 通过率必须按层报，不许只看总平均。

## 为什么

2026-11-22 的样本体检：10 家全是文字版、无金融、7/10 是创业板科创板。
在这种偏"干净"的样本上做出的 96.7%，**外推到全 A 股必然偏乐观**。
总平均数会把困难样本（扫描件、金融业）整个藏起来。

## 分层维度（全部从材料本身算，不预设公司名单）

    载体   文字版 / 扫描件      —— 扫描件是 OCR 路径，从未被测到
    行业   金融 / 非金融        —— 金融没有"营业收入—营业成本"这套科目
    板块   沪市主板 / 科创板 / 创业板 / 深市主板 / 北交所 / 港股 / 美股
    规模   大型(资产>1000亿) / 中型(100–1000亿) / 小型(<100亿)
    盈亏   亏损 / 盈利

**扫描件和 ST 不预设代码**：预先猜哪家是扫描件容易猜错，
应该下载后靠材料本身打标。
"""

from __future__ import annotations

# ⚠️ 不能有「利息收入」——普通公司的财务费用明细里也有它，会大面积误判。
# 用只在金融业出现的科目：已赚保费、手续费及佣金、利息净收入、吸收存款、发放贷款。
_FIN_MARKERS = ("已赚保费", "手续费及佣金", "利息净收入", "吸收存款", "发放贷款")


def board_of(code: str) -> str:
    if not code:
        return "未知"
    if code.isdigit() and len(code) == 5:
        return "港股"
    head3, head1 = code[:3], code[:1]
    if head3 in ("600", "601", "603", "605"):
        return "沪市主板"
    if head3 == "688":
        return "科创板"
    if head3 in ("300", "301"):
        return "创业板"
    if head3 in ("000", "001", "002", "003"):
        return "深市主板"
    if head1 in ("4", "8"):
        return "北交所"
    return "其他"


def _labels(st) -> str:
    out = []
    for kind in ("income", "balance", "cash_flow"):
        blk = getattr(st, kind, None)
        for row in getattr(blk, "rows", []) or []:
            out.append(str(getattr(row, "label", "") or ""))
    return " ".join(out)


def _field(block, *names: str) -> float | None:
    """按字段名取值（Field 枚举的名字各版本可能不同，宽容一点）。"""
    fields = getattr(block, "fields", None) or {}
    for f, v in fields.items():
        nm = getattr(f, "name", str(f))
        if any(n in nm for n in names):
            return v
    return None


def classify(code: str, st) -> dict[str, str]:
    """给一份材料打分层标签。`st` 是 `load_pdf_statements` 的返回。"""
    audited = str(getattr(st, "audited", "") or "")
    warns = " ".join(str(w) for w in (getattr(st, "warnings", None) or []))
    scanned = ("OCR" in audited) or ("OCR" in warns)

    from financials import confidence as _cf
    financial = _cf.is_financial(st)

    total_assets = _field(getattr(st, "balance", None), "TOTAL_ASSETS", "资产总计")
    if total_assets is None:
        size = "未知"
    elif abs(total_assets) >= 1e11:
        size = "大型"
    elif abs(total_assets) >= 1e10:
        size = "中型"
    else:
        size = "小型"

    ni = _field(getattr(st, "income", None), "NET_INCOME", "净利润")
    profit = "未知" if ni is None else ("亏损" if ni < 0 else "盈利")

    return {
        "载体": "扫描件" if scanned else "文字版",
        "行业": "金融" if financial else "非金融",
        "板块": board_of(code),
        "规模": size,
        "盈亏": profit,
    }
